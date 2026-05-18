package main

import (
	"log"
	"net/http"
)

func main() {
	cfg, err := LoadConfig()
	if err != nil {
		log.Fatalf("failed to load config: %v", err)
	}

	k8s, err := NewK8sClient(cfg.JobNamespace)
	if err != nil {
		log.Fatalf("failed to create k8s client: %v", err)
	}

	app := &App{
		cfg: cfg,
		k8s: k8s,
	}

	mux := http.NewServeMux()
	mux.HandleFunc("GET /", app.HandleHome)
	mux.HandleFunc("GET /api/v1/repos", app.HandleListRepos)
	mux.HandleFunc("GET /api/v1/workflows", app.HandleListWorkflows)
	mux.HandleFunc("GET /api/v1/workflows/{job_id}", app.HandleGetWorkflow)
	mux.HandleFunc("POST /api/v1/workflows", app.HandleCreateWorkflow)
	mux.HandleFunc("GET /ci-monitor", app.HandleCIMonitorPage)
	mux.HandleFunc("POST /api/v1/ci-monitor", app.HandleCreateCIMonitor)
	mux.HandleFunc("GET /api/v1/workflows/{job_id}/log", app.HandleWorkflowLogs)

	log.Printf("Orchestrator listening on %s", cfg.ListenAddr)
	log.Fatal(http.ListenAndServe(cfg.ListenAddr, mux))
}
