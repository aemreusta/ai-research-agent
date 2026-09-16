package watchdog

import (
	"path/filepath"
	"runtime"
	"testing"

	c "github.com/aemreusta/apilex-test-case/dispatcher/internal/contracts"
)

func loadMachine(t *testing.T) c.StateMachine {
	t.Helper()
	_, file, _, _ := runtime.Caller(0)
	loaded, err := c.Load(filepath.Join(filepath.Dir(file), "..", "..", "..", "contracts"))
	if err != nil {
		t.Fatal(err)
	}
	return loaded.States
}
