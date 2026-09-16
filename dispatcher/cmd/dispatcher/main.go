// Command dispatcher is the control plane: it claims queued runs, hands them to agent replicas,
// and watches them until they finish. See architecture v0.6 §2.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/signal"
	"sync"
	"syscall"
	"time"

	"github.com/aemreusta/apilex-test-case/dispatcher/internal/agentclient"
	"github.com/aemreusta/apilex-test-case/dispatcher/internal/capacity"
	"github.com/aemreusta/apilex-test-case/dispatcher/internal/config"
	"github.com/aemreusta/apilex-test-case/dispatcher/internal/contracts"
	"github.com/aemreusta/apilex-test-case/dispatcher/internal/dispatcher"
	"github.com/aemreusta/apilex-test-case/dispatcher/internal/logging"
	"github.com/aemreusta/apilex-test-case/dispatcher/internal/store"
)

const healthAddr = ":8090"

func main() {
	healthcheck := flag.Bool("healthcheck", false, "probe a running dispatcher and exit (for the container healthcheck)")
	flag.Parse()
	if *healthcheck {
		os.Exit(probe())
	}
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "dispatcher:", err)
		os.Exit(1)
	}
}

// probe exists because the distroless image has no curl.
func probe() int {
	client := http.Client{Timeout: 2 * time.Second}
	response, err := client.Get("http://127.0.0.1" + healthAddr + "/healthz")
	if err != nil {
		return 1
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return 1
	}
	return 0
}

func run() error {
	env, err := config.LoadEnv(os.Getenv)
	if err != nil {
		return err
	}
	log := logging.New(os.Stdout, env.LogLevel).With("dispatcher_id", env.DispatcherID)

	cfg, err := config.Load(env.ConfigDir)
	if err != nil {
		return err
	}
	shared, err := contracts.Load(env.ContractsDir)
	if err != nil {
		return err
	}
	if cfg.Retry.MaxAttempts != shared.States.MaxAttempts {
		return fmt.Errorf("dispatcher.yaml retry.max_attempts (%d) != run_states.yaml max_attempts (%d)",
			cfg.Retry.MaxAttempts, shared.States.MaxAttempts)
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	db, err := connect(ctx, env.DatabaseURL, shared, log)
	if err != nil {
		return err
	}
	defer db.Close()

	resolver := net.DefaultResolver
	d := &dispatcher.Dispatcher{
		ID:        env.DispatcherID,
		Config:    cfg,
		Contracts: shared,
		Store:     db,
		Agents:    agentclient.New(cfg.HealthTimeout(), cfg.ExecuteTimeout()),
		Discover: func(ctx context.Context) ([]string, error) {
			return capacity.Discover(ctx, env.AgentURLs, resolver,
				cfg.Capacity.AgentServiceHost, cfg.Capacity.AgentPort)
		},
		Log: log,
	}

	server := &http.Server{Addr: healthAddr, ReadHeaderTimeout: 2 * time.Second, Handler: health(db)}
	go func() {
		if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Error("health endpoint failed", "error", err)
		}
	}()

	log.Info("dispatcher started",
		"max_concurrent_runs", cfg.Capacity.MaxConcurrentRuns,
		"heartbeat_timeout", cfg.HeartbeatTimeout().String(),
		"hard_deadline", cfg.HardDeadline().String(),
		"agent_urls", env.AgentURLs,
		"agent_service", cfg.Capacity.AgentServiceHost)

	var wg sync.WaitGroup
	for _, loop := range []func(context.Context){
		d.DispatchLoop,
		d.WatchLoop,
		func(ctx context.Context) { d.ListenLoop(ctx, db.Pool()) },
	} {
		wg.Add(1)
		go func() { defer wg.Done(); loop(ctx) }()
	}
	<-ctx.Done()
	log.Info("shutting down")

	shutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_ = server.Shutdown(shutdown)
	wg.Wait()
	return nil
}

// connect retries for a while: in compose the dispatcher can start before migrations finish.
func connect(ctx context.Context, dsn string, shared contracts.Contracts, log interface {
	Warn(string, ...any)
}) (*store.Store, error) {
	var lastErr error
	for attempt := 1; attempt <= 30; attempt++ {
		db, err := store.New(ctx, dsn, shared.States)
		if err == nil {
			if _, err = db.ActiveCount(ctx); err == nil {
				return db, nil
			}
			db.Close()
		}
		lastErr = err
		log.Warn("database not ready", "attempt", attempt, "error", err)
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-time.After(2 * time.Second):
		}
	}
	return nil, fmt.Errorf("database never became ready: %w", lastErr)
}

func health(db *store.Store) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(r.Context(), time.Second)
		defer cancel()
		if err := db.Pool().Ping(ctx); err != nil {
			http.Error(w, `{"status":"degraded"}`, http.StatusServiceUnavailable)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"status":"ok","service":"dispatcher"}`))
	})
	return mux
}
