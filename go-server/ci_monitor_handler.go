package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"regexp"
)

var prURLPattern = regexp.MustCompile(`^https://github\.com/[^/]+/[^/]+/pull/\d+/?$`)

// CreateCIMonitorRequest is the JSON body for POST /api/v1/ci-monitor.
type CreateCIMonitorRequest struct {
	PRUrls []string `json:"pr_urls"`
}

// HandleCIMonitorPage serves the CI Monitor UI.
func (a *App) HandleCIMonitorPage(w http.ResponseWriter, r *http.Request) {
	data, err := staticFS.ReadFile("static/ci-monitor.html")
	if err != nil {
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Write(data)
}

// HandleCreateCIMonitor creates a K8s Job for CI monitoring.
func (a *App) HandleCreateCIMonitor(w http.ResponseWriter, r *http.Request) {
	var req CreateCIMonitorRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid JSON body")
		return
	}

	if len(req.PRUrls) == 0 {
		writeError(w, http.StatusBadRequest, "pr_urls must contain at least one PR URL")
		return
	}

	if len(req.PRUrls) > 3 {
		writeError(w, http.StatusBadRequest, "pr_urls supports up to 3 PR URLs")
		return
	}

	for _, u := range req.PRUrls {
		if !prURLPattern.MatchString(u) {
			writeError(w, http.StatusBadRequest, fmt.Sprintf("invalid PR URL: %s", u))
			return
		}
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

	params := CIMonitorParams{
		PRUrls:           req.PRUrls,
		WorkerImage:      a.cfg.WorkerImage,
		EnvConfigMap:     a.cfg.WorkerEnvConfigMap,
		GCloudSecret:     a.cfg.GCloudSecretName,
		GHToken:          ghToken,
		GHTokenExpiry:    ghTokenExpiry,
		GHTokenSecret:    "shift-gh-token-" + jobID,
		ConfigsConfigMap: a.cfg.ConfigsConfigMap,
		TTLAfterFinished: a.cfg.TTLAfterFinished,
	}

	if err := a.k8s.CreateCIMonitorJob(r.Context(), jobID, params); err != nil {
		log.Printf("ERROR: creating ci-monitor job: %v", err)
		writeError(w, http.StatusInternalServerError, "failed to create ci-monitor job")
		return
	}

	log.Printf("Created ci-monitor job %s for pr_urls=%v", jobID, req.PRUrls)
	writeJSON(w, http.StatusCreated, CreateWorkflowResponse{
		ID:     jobID,
		Status: "pending",
	})
}
