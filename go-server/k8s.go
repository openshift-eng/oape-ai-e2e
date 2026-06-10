package main

import (
	"context"
	"fmt"
	"io"

	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/clientcmd"
)

// K8sClient wraps a Kubernetes clientset with the target namespace.
type K8sClient struct {
	clientset kubernetes.Interface
	namespace string
}

// NewK8sClient creates a Kubernetes client using in-cluster config,
// falling back to KUBECONFIG for local development.
func NewK8sClient(namespace string) (*K8sClient, error) {
	config, err := rest.InClusterConfig()
	if err != nil {
		// Fall back to kubeconfig for local dev.
		config, err = clientcmd.BuildConfigFromFlags("", clientcmd.RecommendedHomeFile)
		if err != nil {
			return nil, fmt.Errorf("building k8s config: %w", err)
		}
	}

	clientset, err := kubernetes.NewForConfig(config)
	if err != nil {
		return nil, fmt.Errorf("creating k8s clientset: %w", err)
	}

	return &K8sClient{clientset: clientset, namespace: namespace}, nil
}

// WorkflowParams holds the parameters needed to create a workflow K8s Job.
type WorkflowParams struct {
	EPUrl            string
	RepoURL          string
	BaseBranch       string
	DesignDocURL     string
	JiraTicket       string
	WorkerImage      string
	EnvConfigMap     string
	GCloudSecret     string
	GHToken          string
	GHTokenExpiry    string
	GHTokenSecret    string
	ConfigsConfigMap string
	TTLAfterFinished int32
}

// CreateWorkflowJob creates a Kubernetes Job for a workflow run.
func (c *K8sClient) CreateWorkflowJob(ctx context.Context, jobID string, params WorkflowParams) error {
	jobName := "shift-workflow-" + jobID
	secretName := params.GHTokenSecret

	// Create a Secret to hold the GitHub token.
	secret := &corev1.Secret{
		ObjectMeta: metav1.ObjectMeta{
			Name: secretName,
			Labels: map[string]string{
				"app":    "shift-worker",
				"job-id": jobID,
			},
			Annotations: map[string]string{
				"app-platform-shift.openshift.github.io/gh-app-token-expiry": params.GHTokenExpiry,
			},
		},
		StringData: map[string]string{
			"GH_TOKEN": params.GHToken,
		},
	}
	if _, err := c.clientset.CoreV1().Secrets(c.namespace).Create(ctx, secret, metav1.CreateOptions{}); err != nil {
		return fmt.Errorf("creating secret %s: %w", secretName, err)
	}

	backoffLimit := int32(0)
	ttl := params.TTLAfterFinished

	job := &batchv1.Job{
		ObjectMeta: metav1.ObjectMeta{
			Name: jobName,
			Labels: map[string]string{
				"app":    "shift-worker",
				"job-id": jobID,
			},
			Annotations: map[string]string{
				"app-platform-shift.openshift.github.io/repo-url":       params.RepoURL,
				"app-platform-shift.openshift.github.io/ep-url":         params.EPUrl,
				"app-platform-shift.openshift.github.io/base-branch":    params.BaseBranch,
				"app-platform-shift.openshift.github.io/design-doc-url": params.DesignDocURL,
				"app-platform-shift.openshift.github.io/jira-ticket":    params.JiraTicket,
			},
		},
		Spec: batchv1.JobSpec{
			BackoffLimit:            &backoffLimit,
			TTLSecondsAfterFinished: &ttl,
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{
					Labels: map[string]string{
						"app":    "shift-worker",
						"job-id": jobID,
					},
				},
				Spec: corev1.PodSpec{
					RestartPolicy: corev1.RestartPolicyNever,
					Containers: []corev1.Container{
						{
							Name:    "worker",
							Image:   params.WorkerImage,
							Command: []string{"sh", "-c", "python3.11 /app/main.py"},
							Env: []corev1.EnvVar{
								{Name: "EP_URL", Value: params.EPUrl},
								{Name: "REPO_URL", Value: params.RepoURL},
								{Name: "BASE_BRANCH", Value: params.BaseBranch},
								{Name: "DESIGN_DOC_URL", Value: params.DesignDocURL},
								{Name: "JIRA_TICKET", Value: params.JiraTicket},
								{Name: "PYTHONUNBUFFERED", Value: "1"},
								{Name: "GOOGLE_APPLICATION_CREDENTIALS", Value: "/secrets/gcloud/application_default_credentials.json"},
							},
							EnvFrom: []corev1.EnvFromSource{
								{
									ConfigMapRef: &corev1.ConfigMapEnvSource{
										LocalObjectReference: corev1.LocalObjectReference{
											Name: params.EnvConfigMap,
										},
									},
								},
								{
									SecretRef: &corev1.SecretEnvSource{
										LocalObjectReference: corev1.LocalObjectReference{
											Name: secretName,
										},
									},
								},
							},
							Resources: corev1.ResourceRequirements{
								Requests: corev1.ResourceList{
									corev1.ResourceCPU:    resource.MustParse("500m"),
									corev1.ResourceMemory: resource.MustParse("512Mi"),
								},
								Limits: corev1.ResourceList{
									corev1.ResourceCPU:    resource.MustParse("2"),
									corev1.ResourceMemory: resource.MustParse("4Gi"),
								},
							},
							VolumeMounts: []corev1.VolumeMount{
								{
									Name:      "gcloud-adc",
									MountPath: "/secrets/gcloud",
									ReadOnly:  true,
								},
								{
									Name:      "config",
									MountPath: "/config/config.json",
									SubPath:   "config.json",
									ReadOnly:  true,
								},
							},
						},
					},
					Volumes: []corev1.Volume{
						{
							Name: "gcloud-adc",
							VolumeSource: corev1.VolumeSource{
								Secret: &corev1.SecretVolumeSource{
									SecretName: params.GCloudSecret,
								},
							},
						},
						{
							Name: "config",
							VolumeSource: corev1.VolumeSource{
								ConfigMap: &corev1.ConfigMapVolumeSource{
									LocalObjectReference: corev1.LocalObjectReference{
										Name: params.ConfigsConfigMap,
									},
								},
							},
						},
					},
				},
			},
		},
	}

	_, err := c.clientset.BatchV1().Jobs(c.namespace).Create(ctx, job, metav1.CreateOptions{})
	if err != nil {
		return fmt.Errorf("creating job %s: %w", jobName, err)
	}
	return nil
}

// JobStatus represents the current state of a workflow job.
type JobStatus struct {
	Status  string `json:"status"`
	Message string `json:"message,omitempty"`
}

// GetJobStatus returns the status of a workflow K8s Job.
func (c *K8sClient) GetJobStatus(ctx context.Context, jobID string) (*JobStatus, error) {
	jobName := "shift-workflow-" + jobID
	job, err := c.clientset.BatchV1().Jobs(c.namespace).Get(ctx, jobName, metav1.GetOptions{})
	if err != nil {
		return nil, fmt.Errorf("getting job %s: %w", jobName, err)
	}

	for _, cond := range job.Status.Conditions {
		if cond.Type == batchv1.JobComplete && cond.Status == corev1.ConditionTrue {
			return &JobStatus{Status: "succeeded"}, nil
		}
		if cond.Type == batchv1.JobFailed && cond.Status == corev1.ConditionTrue {
			return &JobStatus{Status: "failed", Message: cond.Message}, nil
		}
	}

	if job.Status.Active > 0 {
		return &JobStatus{Status: "running"}, nil
	}

	return &JobStatus{Status: "pending"}, nil
}

// GetJobPod returns the name of the pod created by a workflow Job.
func (c *K8sClient) GetJobPod(ctx context.Context, jobID string) (string, error) {
	jobName := "shift-workflow-" + jobID
	pods, err := c.clientset.CoreV1().Pods(c.namespace).List(ctx, metav1.ListOptions{
		LabelSelector: "job-name=" + jobName,
	})
	if err != nil {
		return "", fmt.Errorf("listing pods for job %s: %w", jobName, err)
	}
	if len(pods.Items) == 0 {
		return "", fmt.Errorf("no pods found for job %s", jobName)
	}
	return pods.Items[0].Name, nil
}

// StreamPodLogs returns a streaming reader of pod logs.
func (c *K8sClient) StreamPodLogs(ctx context.Context, podName string, follow bool) (io.ReadCloser, error) {
	req := c.clientset.CoreV1().Pods(c.namespace).GetLogs(podName, &corev1.PodLogOptions{
		Follow: follow,
	})
	return req.Stream(ctx)
}

// JobInfo contains extended job information from K8s.
type JobInfo struct {
	ID           string
	Status       string
	Message      string
	CreatedAt    string
	RepoURL      string
	EPUrl        string
	BaseBranch   string
	DesignDocURL string
	JiraTicket   string
}

// ListJobs returns all workflow jobs with app=shift-worker label.
func (c *K8sClient) ListJobs(ctx context.Context) ([]JobInfo, error) {
	jobs, err := c.clientset.BatchV1().Jobs(c.namespace).List(ctx, metav1.ListOptions{
		LabelSelector: "app=shift-worker",
	})
	if err != nil {
		return nil, fmt.Errorf("listing jobs: %w", err)
	}

	var result []JobInfo
	for _, job := range jobs.Items {
		jobID := job.Labels["job-id"]
		if jobID == "" {
			continue
		}

		info := JobInfo{
			ID:        jobID,
			CreatedAt: job.CreationTimestamp.Format("2006-01-02T15:04:05Z"),
		}

		// Determine status.
		for _, cond := range job.Status.Conditions {
			if cond.Type == batchv1.JobComplete && cond.Status == corev1.ConditionTrue {
				info.Status = "succeeded"
				break
			}
			if cond.Type == batchv1.JobFailed && cond.Status == corev1.ConditionTrue {
				info.Status = "failed"
				info.Message = cond.Message
				break
			}
		}
		if info.Status == "" {
			if job.Status.Active > 0 {
				info.Status = "running"
			} else {
				info.Status = "pending"
			}
		}

		// Extract metadata from annotations.
		if job.Annotations != nil {
			info.RepoURL = job.Annotations["app-platform-shift.openshift.github.io/repo-url"]
			info.EPUrl = job.Annotations["app-platform-shift.openshift.github.io/ep-url"]
			info.BaseBranch = job.Annotations["app-platform-shift.openshift.github.io/base-branch"]
			info.DesignDocURL = job.Annotations["app-platform-shift.openshift.github.io/design-doc-url"]
			info.JiraTicket = job.Annotations["app-platform-shift.openshift.github.io/jira-ticket"]
		}

		result = append(result, info)
	}

	return result, nil
}

// GetJobInfo returns extended information for a single job.
func (c *K8sClient) GetJobInfo(ctx context.Context, jobID string) (*JobInfo, error) {
	jobName := "shift-workflow-" + jobID
	job, err := c.clientset.BatchV1().Jobs(c.namespace).Get(ctx, jobName, metav1.GetOptions{})
	if err != nil {
		return nil, fmt.Errorf("getting job %s: %w", jobName, err)
	}

	info := &JobInfo{
		ID:        jobID,
		CreatedAt: job.CreationTimestamp.Format("2006-01-02T15:04:05Z"),
	}

	// Determine status.
	for _, cond := range job.Status.Conditions {
		if cond.Type == batchv1.JobComplete && cond.Status == corev1.ConditionTrue {
			info.Status = "succeeded"
			break
		}
		if cond.Type == batchv1.JobFailed && cond.Status == corev1.ConditionTrue {
			info.Status = "failed"
			info.Message = cond.Message
			break
		}
	}
	if info.Status == "" {
		if job.Status.Active > 0 {
			info.Status = "running"
		} else {
			info.Status = "pending"
		}
	}

	// Extract metadata from annotations.
	if job.Annotations != nil {
		info.RepoURL = job.Annotations["app-platform-shift.openshift.github.io/repo-url"]
		info.EPUrl = job.Annotations["app-platform-shift.openshift.github.io/ep-url"]
		info.BaseBranch = job.Annotations["app-platform-shift.openshift.github.io/base-branch"]
		info.DesignDocURL = job.Annotations["app-platform-shift.openshift.github.io/design-doc-url"]
		info.JiraTicket = job.Annotations["app-platform-shift.openshift.github.io/jira-ticket"]
	}

	return info, nil
}
