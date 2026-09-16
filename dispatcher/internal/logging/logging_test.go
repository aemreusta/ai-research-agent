package logging

import (
	"bytes"
	"encoding/json"
	"testing"
)

func TestFieldNamesMatchThePythonServices(t *testing.T) {
	var buffer bytes.Buffer
	New(&buffer, "INFO").Info("run claimed", "run_id", "r1", "node", "dispatcher")
	var line map[string]any
	if err := json.Unmarshal(buffer.Bytes(), &line); err != nil {
		t.Fatal(err)
	}
	for key, want := range map[string]any{
		"event": "run claimed", "service": "dispatcher", "run_id": "r1", "level": "info",
	} {
		if line[key] != want {
			t.Errorf("%s = %v, want %v", key, line[key], want)
		}
	}
	if _, ok := line["timestamp"]; !ok {
		t.Error("timestamp missing")
	}
}

func TestLevelFiltering(t *testing.T) {
	var buffer bytes.Buffer
	logger := New(&buffer, "WARNING")
	logger.Info("hidden")
	logger.Warn("shown")
	if bytes.Contains(buffer.Bytes(), []byte("hidden")) || !bytes.Contains(buffer.Bytes(), []byte("shown")) {
		t.Fatalf("output = %s", buffer.String())
	}
}
