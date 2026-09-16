package config

import (
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func repoConfigDir(t *testing.T) string {
	t.Helper()
	_, file, _, _ := runtime.Caller(0)
	return filepath.Join(filepath.Dir(file), "..", "..", "..", "config")
}

func TestTheShippedConfigLoadsAndValidates(t *testing.T) {
	cfg, err := Load(repoConfigDir(t))
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	if cfg.HeartbeatTimeout() != 30*time.Second {
		t.Errorf("heartbeat timeout = %s", cfg.HeartbeatTimeout())
	}
	if cfg.Capacity.AgentServiceHost != "agent" {
		t.Errorf("agent host = %q", cfg.Capacity.AgentServiceHost)
	}
}

func TestBackoffGrowsAndThenHolds(t *testing.T) {
	var cfg Config
	cfg.Retry.BackoffSeconds = []int{5, 15}
	cases := map[int]time.Duration{1: 0, 2: 5 * time.Second, 3: 15 * time.Second, 9: 15 * time.Second}
	for attempt, want := range cases {
		if got := cfg.Backoff(attempt); got != want {
			t.Errorf("Backoff(%d) = %s, want %s", attempt, got, want)
		}
	}
}

func TestATickLongerThanTheHeartbeatTimeoutIsRejected(t *testing.T) {
	cfg, err := Load(repoConfigDir(t))
	if err != nil {
		t.Fatal(err)
	}
	cfg.Watchdog.TickSeconds = 60
	if err := cfg.Validate(); err == nil || !strings.Contains(err.Error(), "tick_seconds") {
		t.Fatalf("expected a tick/heartbeat error, got %v", err)
	}
}

func TestZeroValuesAreRejected(t *testing.T) {
	if err := (Config{}).Validate(); err == nil {
		t.Fatal("an empty config must not validate")
	}
}

func env(values map[string]string) func(string) string {
	return func(key string) string { return values[key] }
}

func TestTheDispatcherRefusesToHoldProviderKeys(t *testing.T) {
	for _, name := range []string{"OPENAI_API_KEY", "TAVILY_API_KEY", "APP_SECRET_KEY"} {
		_, err := LoadEnv(env(map[string]string{
			"DATABASE_URL": "postgresql://u:p@h/db",
			name:           "anything",
		}))
		if err == nil || !strings.Contains(err.Error(), "must not hold secrets") {
			t.Errorf("%s: expected refusal, got %v", name, err)
		}
	}
}

func TestTheSQLAlchemyURLSpellingIsAccepted(t *testing.T) {
	for _, raw := range []string{
		"postgresql+psycopg://u:p@postgres:5432/research",
		"postgresql+asyncpg://u:p@postgres:5432/research",
		"postgresql://u:p@postgres:5432/research",
	} {
		got, err := LoadEnv(env(map[string]string{"DATABASE_URL": raw}))
		if err != nil {
			t.Fatalf("%s: %v", raw, err)
		}
		if got.DatabaseURL != "postgres://u:p@postgres:5432/research" {
			t.Errorf("%s -> %s", raw, got.DatabaseURL)
		}
	}
}

func TestANonPostgresURLIsRejected(t *testing.T) {
	if _, err := LoadEnv(env(map[string]string{"DATABASE_URL": "mysql://x"})); err == nil {
		t.Fatal("expected an error")
	}
}

func TestAgentURLsOverrideDiscovery(t *testing.T) {
	got, err := LoadEnv(env(map[string]string{
		"DATABASE_URL": "postgresql://u:p@h/db",
		"AGENT_URLS":   " http://a:8081/ , http://b:8081 ,",
	}))
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"http://a:8081", "http://b:8081"}
	if strings.Join(got.AgentURLs, ",") != strings.Join(want, ",") {
		t.Fatalf("got %v", got.AgentURLs)
	}
}
