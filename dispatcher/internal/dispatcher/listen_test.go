package dispatcher

import "testing"

func TestOnlyStatusChangesCountAsCapacityHints(t *testing.T) {
	if !isPlainEvent(`{"run_id": "r", "seq": 4}`) {
		t.Error("a timeline entry is a plain event")
	}
	if isPlainEvent(`{"run_id": "r", "status": "succeeded"}`) {
		t.Error("a status change is not a plain event")
	}
}
