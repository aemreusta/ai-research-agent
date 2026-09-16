// Package config loads config/dispatcher.yaml and the few environment values the dispatcher
// needs. The dispatcher never reads provider keys: the control plane is secret-free by design
// (architecture v0.6 §2), and Load refuses to start if it is handed any.
package config

import (
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"

	"gopkg.in/yaml.v3"
)

// Config is the typed view of config/dispatcher.yaml.
type Config struct {
	Version int `yaml:"version"`
	Queue   struct {
		PollIntervalSeconds int `yaml:"poll_interval_seconds"`
	} `yaml:"queue"`
	Capacity struct {
		MaxConcurrentRuns int    `yaml:"max_concurrent_runs"`
		AgentServiceHost  string `yaml:"agent_service_host"`
		AgentPort         int    `yaml:"agent_port"`
	} `yaml:"capacity"`
	Watchdog struct {
		TickSeconds             int `yaml:"tick_seconds"`
		HeartbeatTimeoutSeconds int `yaml:"heartbeat_timeout_seconds"`
		DispatchTimeoutSeconds  int `yaml:"dispatch_timeout_seconds"`
		CancelGraceSeconds      int `yaml:"cancel_grace_seconds"`
	} `yaml:"watchdog"`
	Deadline struct {
		HardDeadlineSeconds    int `yaml:"hard_deadline_seconds"`
		WallClockMarginSeconds int `yaml:"wall_clock_margin_seconds"`
	} `yaml:"deadline"`
	Retry struct {
		MaxAttempts    int   `yaml:"max_attempts"`
		BackoffSeconds []int `yaml:"backoff_seconds"`
	} `yaml:"retry"`
	AgentClient struct {
		ExecuteTimeoutSeconds int `yaml:"execute_timeout_seconds"`
		HealthTimeoutSeconds  int `yaml:"health_timeout_seconds"`
	} `yaml:"agent_client"`
}

// Durations, so callers never multiply by time.Second themselves.
func (c Config) PollInterval() time.Duration     { return seconds(c.Queue.PollIntervalSeconds) }
func (c Config) WatchdogTick() time.Duration     { return seconds(c.Watchdog.TickSeconds) }
func (c Config) HeartbeatTimeout() time.Duration { return seconds(c.Watchdog.HeartbeatTimeoutSeconds) }
func (c Config) DispatchTimeout() time.Duration  { return seconds(c.Watchdog.DispatchTimeoutSeconds) }
func (c Config) CancelGrace() time.Duration      { return seconds(c.Watchdog.CancelGraceSeconds) }
func (c Config) HardDeadline() time.Duration     { return seconds(c.Deadline.HardDeadlineSeconds) }
func (c Config) WallClockMargin() time.Duration  { return seconds(c.Deadline.WallClockMarginSeconds) }
func (c Config) ExecuteTimeout() time.Duration   { return seconds(c.AgentClient.ExecuteTimeoutSeconds) }
func (c Config) HealthTimeout() time.Duration    { return seconds(c.AgentClient.HealthTimeoutSeconds) }

// Backoff returns how long to wait before dispatching attempt n (1-based) again.
func (c Config) Backoff(attempt int) time.Duration {
	if attempt <= 1 || len(c.Retry.BackoffSeconds) == 0 {
		return 0
	}
	index := min(attempt-2, len(c.Retry.BackoffSeconds)-1)
	return seconds(c.Retry.BackoffSeconds[index])
}

func seconds(n int) time.Duration { return time.Duration(n) * time.Second }

// Validate rejects values that would make the control plane unsafe rather than merely slow.
func (c Config) Validate() error {
	var problems []string
	positive := map[string]int{
		"queue.poll_interval_seconds":          c.Queue.PollIntervalSeconds,
		"capacity.max_concurrent_runs":         c.Capacity.MaxConcurrentRuns,
		"capacity.agent_port":                  c.Capacity.AgentPort,
		"watchdog.tick_seconds":                c.Watchdog.TickSeconds,
		"watchdog.heartbeat_timeout_seconds":   c.Watchdog.HeartbeatTimeoutSeconds,
		"watchdog.dispatch_timeout_seconds":    c.Watchdog.DispatchTimeoutSeconds,
		"deadline.hard_deadline_seconds":       c.Deadline.HardDeadlineSeconds,
		"retry.max_attempts":                   c.Retry.MaxAttempts,
		"agent_client.execute_timeout_seconds": c.AgentClient.ExecuteTimeoutSeconds,
		"agent_client.health_timeout_seconds":  c.AgentClient.HealthTimeoutSeconds,
	}
	for name, value := range positive {
		if value <= 0 {
			problems = append(problems, fmt.Sprintf("%s must be > 0 (got %d)", name, value))
		}
	}
	if c.Watchdog.HeartbeatTimeoutSeconds > 0 &&
		c.Watchdog.TickSeconds >= c.Watchdog.HeartbeatTimeoutSeconds {
		problems = append(problems,
			"watchdog.tick_seconds must be shorter than heartbeat_timeout_seconds, "+
				"or a lost agent is noticed a whole tick late")
	}
	if len(problems) > 0 {
		return fmt.Errorf("invalid dispatcher config: %s", strings.Join(problems, "; "))
	}
	return nil
}

// Load reads dispatcher.yaml from dir (APP_CONFIG_DIR, or ./config).
func Load(dir string) (Config, error) {
	var cfg Config
	raw, err := os.ReadFile(filepath.Join(dir, "dispatcher.yaml"))
	if err != nil {
		return cfg, fmt.Errorf("read dispatcher.yaml: %w", err)
	}
	decoder := yaml.NewDecoder(strings.NewReader(string(raw)))
	decoder.KnownFields(true)
	if err := decoder.Decode(&cfg); err != nil {
		return cfg, fmt.Errorf("parse dispatcher.yaml: %w", err)
	}
	return cfg, cfg.Validate()
}

// Env is what the process takes from its environment.
type Env struct {
	DatabaseURL  string
	ConfigDir    string
	ContractsDir string
	AgentURLs    []string
	DispatcherID string
	LogLevel     string
}

// forbidden lists variables the control plane must never be given (v0.6 §15.3).
var forbidden = []string{
	"GEMINI_API_KEY", "OPENAI_API_KEY", "TAVILY_API_KEY", "BRAVE_API_KEY",
	"APP_SECRET_KEY", "APP_SECRET_KEY_FILE",
}

// LoadEnv reads the environment and enforces the no-secrets rule.
func LoadEnv(getenv func(string) string) (Env, error) {
	for _, name := range forbidden {
		if getenv(name) != "" {
			return Env{}, fmt.Errorf(
				"%s is set: the dispatcher is the control plane and must not hold secrets", name)
		}
	}
	dsn, err := normaliseDSN(getenv("DATABASE_URL"))
	if err != nil {
		return Env{}, err
	}
	env := Env{
		DatabaseURL:  dsn,
		ConfigDir:    orDefault(getenv("APP_CONFIG_DIR"), "config"),
		ContractsDir: orDefault(getenv("APP_CONTRACTS_DIR"), "contracts"),
		DispatcherID: orDefault(getenv("DISPATCHER_ID"), orDefault(getenv("HOSTNAME"), "dispatcher")),
		LogLevel:     orDefault(getenv("APP_LOG_LEVEL"), "INFO"),
	}
	for _, item := range strings.Split(getenv("AGENT_URLS"), ",") {
		if item = strings.TrimSpace(item); item != "" {
			env.AgentURLs = append(env.AgentURLs, strings.TrimRight(item, "/"))
		}
	}
	return env, nil
}

// normaliseDSN accepts the SQLAlchemy spelling used in .env (postgresql+psycopg://) and
// returns the libpq one pgx expects, so both languages read the same DATABASE_URL.
func normaliseDSN(raw string) (string, error) {
	if raw == "" {
		return "", errors.New("DATABASE_URL is not set")
	}
	parsed, err := url.Parse(raw)
	if err != nil {
		return "", fmt.Errorf("DATABASE_URL: %w", err)
	}
	scheme, _, _ := strings.Cut(parsed.Scheme, "+")
	if scheme != "postgresql" && scheme != "postgres" {
		return "", fmt.Errorf("DATABASE_URL must be PostgreSQL, got %q", parsed.Scheme)
	}
	parsed.Scheme = "postgres"
	return parsed.String(), nil
}

func orDefault(value, fallback string) string {
	if value == "" {
		return fallback
	}
	return value
}
