package agentclient

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"gopkg.in/yaml.v3"
)

const runID = "0b4f2c1e-4a7e-4a1f-9c1b-7c1d2e3f4a5b"

func client() *Client { return New(time.Second, time.Second) }

func TestExecuteSendsTheContractBodyAndAccepts202(t *testing.T) {
	var got map[string]any
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != fmt.Sprintf(PathExecute, runID) {
			t.Errorf("unexpected %s %s", r.Method, r.URL.Path)
		}
		if r.Header.Get("Content-Type") != "application/json" {
			t.Errorf("content type = %q", r.Header.Get("Content-Type"))
		}
		_ = json.NewDecoder(r.Body).Decode(&got)
		w.WriteHeader(http.StatusAccepted)
		_, _ = w.Write([]byte(`{"accepted":true,"run_id":"` + runID + `","agent_id":"a1"}`))
	}))
	defer server.Close()

	deadline := time.Date(2026, 9, 16, 12, 30, 0, 0, time.UTC)
	err := client().Execute(context.Background(), server.URL, runID,
		ExecuteRequest{Attempt: 2, LeaseID: runID, DeadlineAt: &deadline, DispatcherID: "d1"})
	if err != nil {
		t.Fatal(err)
	}
	if got["attempt"] != float64(2) || got["dispatcher_id"] != "d1" || got["lease_id"] != runID {
		t.Errorf("body = %v", got)
	}
	for key := range got {
		if key != "attempt" && key != "deadline_at" && key != "dispatcher_id" && key != "lease_id" {
			t.Errorf("unexpected field %q: the agent rejects unknown fields", key)
		}
	}
}

func TestExecuteMapsStatusesToMeanings(t *testing.T) {
	cases := map[int]error{
		http.StatusServiceUnavailable:  ErrNoCapacity,
		http.StatusConflict:            ErrAlreadyRunning,
		http.StatusNotFound:            ErrRunNotFound,
		http.StatusInternalServerError: ErrUnreachable,
	}
	for status, want := range cases {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			w.WriteHeader(status)
			_, _ = w.Write([]byte(`{"error_code":"DISPATCH_DEFERRED","message":"busy","expected":true}`))
		}))
		err := client().Execute(context.Background(), server.URL, runID, ExecuteRequest{Attempt: 1})
		server.Close()
		if !errors.Is(err, want) {
			t.Errorf("status %d: got %v, want %v", status, err, want)
		}
	}
}

func TestAnUnreachableAgentIsReportedAsSuch(t *testing.T) {
	err := client().Execute(context.Background(), "http://127.0.0.1:1", runID, ExecuteRequest{Attempt: 1})
	if !errors.Is(err, ErrUnreachable) {
		t.Fatalf("got %v", err)
	}
}

func TestASlowAgentTimesOut(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		time.Sleep(300 * time.Millisecond)
		w.WriteHeader(http.StatusAccepted)
	}))
	defer server.Close()
	c := New(50*time.Millisecond, 50*time.Millisecond)
	if err := c.Execute(context.Background(), server.URL, runID, ExecuteRequest{Attempt: 1}); !errors.Is(err, ErrUnreachable) {
		t.Fatalf("got %v", err)
	}
}

func TestADrainingAgentIsNotHealthy(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
		_, _ = w.Write([]byte(`{"status":"draining","agent_id":"a1","version":"0.1.0"}`))
	}))
	defer server.Close()
	if _, err := client().Health(context.Background(), server.URL); err == nil {
		t.Fatal("a draining replica must not be treated as healthy")
	}
}

func TestCapacityAndCancelDecode(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case PathCapacity:
			_, _ = w.Write([]byte(`{"agent_id":"a1","slots_total":2,"slots_used":1,"slots_free":1,"running_run_ids":[]}`))
		case fmt.Sprintf(PathCancel, runID):
			w.WriteHeader(http.StatusAccepted)
			_, _ = w.Write([]byte(`{"accepted":true,"run_id":"` + runID + `","was_running":true}`))
		}
	}))
	defer server.Close()

	capacity, err := client().Capacity(context.Background(), server.URL)
	if err != nil || capacity.SlotsFree != 1 || capacity.AgentID != "a1" {
		t.Fatalf("capacity = %+v, %v", capacity, err)
	}
	wasRunning, err := client().Cancel(context.Background(), server.URL, runID, "deadline_exceeded")
	if err != nil || !wasRunning {
		t.Fatalf("cancel = %v, %v", wasRunning, err)
	}
}

// Contract: every path the client calls is declared in the shared OpenAPI file, and every
// field it sends to execute is a declared property.
func TestTheClientOnlyUsesWhatTheContractDeclares(t *testing.T) {
	_, file, _, _ := runtime.Caller(0)
	raw, err := os.ReadFile(filepath.Join(filepath.Dir(file), "..", "..", "..", "contracts", "agent-api.openapi.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	var spec struct {
		Paths      map[string]map[string]any `yaml:"paths"`
		Components struct {
			Schemas map[string]struct {
				Required   []string       `yaml:"required"`
				Properties map[string]any `yaml:"properties"`
			} `yaml:"schemas"`
		} `yaml:"components"`
	}
	if err := yaml.Unmarshal(raw, &spec); err != nil {
		t.Fatal(err)
	}

	for _, path := range []string{PathHealthz, PathCapacity, PathExecute, PathCancel} {
		declared := strings.ReplaceAll(path, "%s", "{run_id}")
		if _, ok := spec.Paths[declared]; !ok {
			t.Errorf("client calls %s, which the contract does not declare", declared)
		}
	}

	encoded, _ := json.Marshal(ExecuteRequest{Attempt: 1, DeadlineAt: &time.Time{}, DispatcherID: "d"})
	var sent map[string]any
	_ = json.Unmarshal(encoded, &sent)
	properties := spec.Components.Schemas["ExecuteRequest"].Properties
	for field := range sent {
		if _, ok := properties[field]; !ok {
			t.Errorf("client sends %q, not a declared ExecuteRequest property", field)
		}
	}
	for _, required := range spec.Components.Schemas["ExecuteRequest"].Required {
		if _, ok := sent[required]; !ok {
			t.Errorf("contract requires %q, client does not send it", required)
		}
	}
}
