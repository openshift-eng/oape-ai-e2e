package main

import (
	"bufio"
	"crypto/rand"
	"embed"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"regexp"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

//go:embed static/homepage.html
var staticFS embed.FS

// App holds shared dependencies for HTTP handlers.
type App struct {
	cfg *ServerConfig
	k8s *K8sClient
}

var epURLPattern = regexp.MustCompile(`^https://github\.com/openshift/enhancements/pull/\d+/?$`)
var gistURLPattern = regexp.MustCompile(`^https://gist\.github(usercontent)?\.com/`)

// CreateWorkflowRequest is the JSON body for POST /api/v1/workflows.
type CreateWorkflowRequest struct {
	EPUrl        string `json:"ep_url"`
	BaseBranch   string `json:"base_branch"`
	RepoURL      string `json:"repo_url"`
	DesignDocURL string `json:"design_doc_url,omitempty"`
	JiraTicket   string `json:"jira_ticket,omitempty"`
}

// WorkflowSummary is a compact representation for workflow lists.
type WorkflowSummary struct {
	ID        string `json:"id"`
	Status    string `json:"status"`
	CreatedAt string `json:"createdAt"`
	RepoURL   string `json:"repoUrl"`
}

// WorkflowListResponse for GET /api/v1/workflows.
type WorkflowListResponse struct {
	Items []WorkflowSummary `json:"items"`
}

// RepoListResponse for GET /api/v1/repos.
type RepoListResponse struct {
	Items []RepoInfo `json:"items"`
}

// WorkflowDetailResponse for GET /api/v1/workflows/{job_id}.
type WorkflowDetailResponse struct {
	ID           string `json:"id"`
	Status       string `json:"status"`
	Message      string `json:"message,omitempty"`
	CreatedAt    string `json:"createdAt"`
	RepoURL      string `json:"repoUrl"`
	EPUrl        string `json:"epUrl"`
	BaseBranch   string `json:"baseBranch"`
	DesignDocURL string `json:"designDocUrl,omitempty"`
	JiraTicket   string `json:"jiraTicket,omitempty"`
}

// CreateWorkflowResponse for POST /api/v1/workflows.
type CreateWorkflowResponse struct {
	ID     string `json:"id"`
	Status string `json:"status"`
}

// fetchGHToken requests a fresh GitHub App installation token from the ghpat HTTP service.
// Returns the token and its expiry timestamp (ISO 8601 format).
func fetchGHToken(serviceURL string) (token string, expiresAt string, err error) {
	resp, err := http.Get(serviceURL + "/token")
	if err != nil {
		return "", "", fmt.Errorf("requesting token from ghpat service: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", "", fmt.Errorf("reading ghpat response: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		return "", "", fmt.Errorf("ghpat service returned %d: %s", resp.StatusCode, body)
	}

	var result struct {
		Token     string `json:"token"`
		ExpiresAt string `json:"expires_at"`
	}
	if err := json.Unmarshal(body, &result); err != nil {
		return "", "", fmt.Errorf("parsing ghpat response: %w", err)
	}
	if result.Token == "" {
		return "", "", fmt.Errorf("ghpat service returned empty token")
	}
	return result.Token, result.ExpiresAt, nil
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}

func writeError(w http.ResponseWriter, code int, msg string) {
	writeJSON(w, code, map[string]string{"detail": msg})
}

func generateJobID() (string, error) {
	b := make([]byte, 6)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return hex.EncodeToString(b), nil
}

// HandleHome serves the UI.
func (a *App) HandleHome(w http.ResponseWriter, r *http.Request) {
	data, err := staticFS.ReadFile("static/homepage.html")
	if err != nil {
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Write(data)
}

// HandleListRepos returns the list of allowed repositories.
func (a *App) HandleListRepos(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, RepoListResponse{
		Items: a.cfg.TeamRepos,
	})
}

// HandleCreateWorkflow creates a K8s Job for a workflow run.
func (a *App) HandleCreateWorkflow(w http.ResponseWriter, r *http.Request) {
	var req CreateWorkflowRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid JSON body")
		return
	}

	if req.RepoURL == "" || req.BaseBranch == "" {
		writeError(w, http.StatusBadRequest, "repo_url and base_branch are required")
		return
	}

	if req.EPUrl == "" && req.DesignDocURL == "" && req.JiraTicket == "" {
		writeError(w, http.StatusBadRequest, "at least one input source is required: ep_url, design_doc_url, or jira_ticket")
		return
	}

	if req.EPUrl != "" && !epURLPattern.MatchString(req.EPUrl) {
		writeError(w, http.StatusBadRequest, "ep_url must be a valid OpenShift enhancement PR URL")
		return
	}

	if req.DesignDocURL != "" && !gistURLPattern.MatchString(req.DesignDocURL) {
		writeError(w, http.StatusBadRequest, "design_doc_url must be a valid GitHub Gist URL")
		return
	}

	jobID, err := generateJobID()
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to generate job ID")
		return
	}

	ghToken, ghTokenExpiry, err := fetchGHToken(a.cfg.GHTokenServiceURL)
	if err != nil {
		log.Printf("ERROR: fetching GH token: %v", err)
		writeError(w, http.StatusInternalServerError, "failed to fetch GitHub token")
		return
	}

	params := WorkflowParams{
		EPUrl:            req.EPUrl,
		RepoURL:          req.RepoURL,
		BaseBranch:       req.BaseBranch,
		DesignDocURL:     req.DesignDocURL,
		JiraTicket:       req.JiraTicket,
		WorkerImage:      a.cfg.WorkerImage,
		EnvConfigMap:     a.cfg.WorkerEnvConfigMap,
		GCloudSecret:     a.cfg.GCloudSecretName,
		GHToken:          ghToken,
		GHTokenExpiry:    ghTokenExpiry,
		GHTokenSecret:    "shift-gh-token-" + jobID,
		ConfigsConfigMap: a.cfg.ConfigsConfigMap,
		TTLAfterFinished: a.cfg.TTLAfterFinished,
	}

	if err := a.k8s.CreateWorkflowJob(r.Context(), jobID, params); err != nil {
		log.Printf("ERROR: creating job: %v", err)
		writeError(w, http.StatusInternalServerError, "failed to create workflow job")
		return
	}

	log.Printf("Created workflow job %s for ep=%s design_doc=%s jira=%s repo=%s base_branch=%s", jobID, req.EPUrl, req.DesignDocURL, req.JiraTicket, req.RepoURL, req.BaseBranch)
	writeJSON(w, http.StatusCreated, CreateWorkflowResponse{
		ID:     jobID,
		Status: "pending",
	})
}

// HandleListWorkflows returns all workflow jobs.
func (a *App) HandleListWorkflows(w http.ResponseWriter, r *http.Request) {
	jobs, err := a.k8s.ListJobs(r.Context())
	if err != nil {
		log.Printf("ERROR: listing workflows: %v", err)
		writeError(w, http.StatusInternalServerError, "failed to list workflows")
		return
	}

	items := make([]WorkflowSummary, len(jobs))
	for i, j := range jobs {
		items[i] = WorkflowSummary{
			ID:        j.ID,
			Status:    j.Status,
			CreatedAt: j.CreatedAt,
			RepoURL:   j.RepoURL,
		}
	}

	writeJSON(w, http.StatusOK, WorkflowListResponse{Items: items})
}

// HandleGetWorkflow returns details of a specific workflow.
func (a *App) HandleGetWorkflow(w http.ResponseWriter, r *http.Request) {
	jobID := r.PathValue("job_id")
	if jobID == "" {
		writeError(w, http.StatusBadRequest, "job_id is required")
		return
	}

	info, err := a.k8s.GetJobInfo(r.Context(), jobID)
	if err != nil {
		writeError(w, http.StatusNotFound, fmt.Sprintf("workflow not found: %s", jobID))
		return
	}

	writeJSON(w, http.StatusOK, WorkflowDetailResponse{
		ID:           info.ID,
		Status:       info.Status,
		Message:      info.Message,
		CreatedAt:    info.CreatedAt,
		RepoURL:      info.RepoURL,
		EPUrl:        info.EPUrl,
		BaseBranch:   info.BaseBranch,
		DesignDocURL: info.DesignDocURL,
		JiraTicket:   info.JiraTicket,
	})
}

// HandleWorkflowLogs streams pod logs as SSE events.
func (a *App) HandleWorkflowLogs(w http.ResponseWriter, r *http.Request) {
	jobID := r.PathValue("job_id")
	if jobID == "" {
		writeError(w, http.StatusBadRequest, "job_id is required")
		return
	}

	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming not supported", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")

	ctx := r.Context()

	// Wait for the pod to be available (up to 5 minutes).
	var podName string
	deadline := time.Now().Add(5 * time.Minute)
	for {
		if time.Now().After(deadline) {
			fmt.Fprintf(w, "event: complete\ndata: %s\n\n",
				`{"status":"failed","message":"timed out waiting for pod"}`)
			flusher.Flush()
			return
		}

		select {
		case <-ctx.Done():
			return
		default:
		}

		name, err := a.k8s.GetJobPod(ctx, jobID)
		if err == nil {
			podName = name
			break
		}

		fmt.Fprintf(w, "event: status\ndata: %s\n\n",
			`{"status":"waiting_for_pod"}`)
		flusher.Flush()

		select {
		case <-ctx.Done():
			return
		case <-time.After(2 * time.Second):
		}
	}

	// Wait for pod to be running or terminated.
	for {
		select {
		case <-ctx.Done():
			return
		default:
		}

		pod, err := a.k8s.clientset.CoreV1().Pods(a.k8s.namespace).Get(ctx, podName, metav1.GetOptions{})
		if err != nil {
			fmt.Fprintf(w, "event: status\ndata: %s\n\n",
				`{"status":"waiting_for_pod"}`)
			flusher.Flush()
			time.Sleep(2 * time.Second)
			continue
		}

		phase := pod.Status.Phase
		if phase == corev1.PodRunning || phase == corev1.PodSucceeded || phase == corev1.PodFailed {
			break
		}

		fmt.Fprintf(w, "event: status\ndata: %s\n\n",
			fmt.Sprintf(`{"status":"pod_%s"}`, string(phase)))
		flusher.Flush()

		select {
		case <-ctx.Done():
			return
		case <-time.After(2 * time.Second):
		}
	}

	// Stream pod logs.
	logStream, err := a.k8s.StreamPodLogs(ctx, podName, true)
	if err != nil {
		log.Printf("ERROR: streaming logs for pod %s: %v", podName, err)
		fmt.Fprintf(w, "event: complete\ndata: %s\n\n",
			`{"status":"failed","message":"failed to stream logs"}`)
		flusher.Flush()
		return
	}
	defer logStream.Close()

	scanner := bufio.NewScanner(logStream)
	// Increase scanner buffer for long log lines.
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)

	for scanner.Scan() {
		select {
		case <-ctx.Done():
			return
		default:
		}

		line := scanner.Text()
		fmt.Fprintf(w, "event: log\ndata: %s\n\n", line)
		flusher.Flush()
	}

	// Log stream ended — get final job status.
	status, err := a.k8s.GetJobStatus(ctx, jobID)
	if err != nil {
		fmt.Fprintf(w, "event: complete\ndata: %s\n\n",
			`{"status":"unknown","message":"could not determine final status"}`)
	} else {
		data, _ := json.Marshal(status)
		fmt.Fprintf(w, "event: complete\ndata: %s\n\n", data)
	}
	flusher.Flush()
}
