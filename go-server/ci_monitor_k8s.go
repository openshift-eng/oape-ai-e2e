package main

import (
	"context"
	"fmt"
	"strings"

	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// CIMonitorParams holds parameters for a ci-monitor K8s Job.
type CIMonitorParams struct {
	PRUrls           []string
	WorkerImage      string
	EnvConfigMap     string
	GCloudSecret     string
	GHToken          string
	GHTokenExpiry    string
	GHTokenSecret    string
	ConfigsConfigMap string
	TTLAfterFinished int32
}

// CreateCIMonitorJob creates a Kubernetes Job for CI monitoring.
func (c *K8sClient) CreateCIMonitorJob(ctx context.Context, jobID string, params CIMonitorParams) error {
	jobName := "shift-ci-monitor-" + jobID
	secretName := params.GHTokenSecret

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
	prURLsJoined := strings.Join(params.PRUrls, " ")

	job := &batchv1.Job{
		ObjectMeta: metav1.ObjectMeta{
			Name: jobName,
			Labels: map[string]string{
				"app":    "shift-worker",
				"job-id": jobID,
			},
			Annotations: map[string]string{
				"app-platform-shift.openshift.github.io/workflow-type": "ci-monitor",
				"app-platform-shift.openshift.github.io/pr-urls":      prURLsJoined,
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
								{Name: "WORKFLOW_TYPE", Value: "ci-monitor"},
								{Name: "PR_URLS", Value: prURLsJoined},
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
