package capacity

import (
	"context"
	"errors"
	"reflect"
	"testing"
)

type fakeResolver struct {
	addresses []string
	err       error
}

func (f fakeResolver) LookupHost(context.Context, string) ([]string, error) {
	return f.addresses, f.err
}

func TestStaticURLsWinOverDiscovery(t *testing.T) {
	got, err := Discover(context.Background(), []string{"http://a:1"}, fakeResolver{err: errors.New("unused")}, "agent", 8081)
	if err != nil || !reflect.DeepEqual(got, []string{"http://a:1"}) {
		t.Fatalf("got %v, %v", got, err)
	}
}

func TestEveryScaledReplicaIsDiscovered(t *testing.T) {
	got, err := Discover(context.Background(), nil, fakeResolver{addresses: []string{"172.18.0.7", "172.18.0.5"}}, "agent", 8081)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"http://172.18.0.5:8081", "http://172.18.0.7:8081"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v", got)
	}
}

func TestDiscoveryFailureIsAnError(t *testing.T) {
	if _, err := Discover(context.Background(), nil, fakeResolver{err: errors.New("nxdomain")}, "agent", 8081); err == nil {
		t.Fatal("expected an error")
	}
}

func TestRankPrefersTheEmptiestHealthyReplica(t *testing.T) {
	got := Rank([]Replica{
		{URL: "http://c", Healthy: true, SlotsFree: 1},
		{URL: "http://a", Healthy: false, SlotsFree: 5},
		{URL: "http://b", Healthy: true, SlotsFree: 2},
		{URL: "http://d", Healthy: true, SlotsFree: 0},
		{URL: "http://e", Healthy: true, SlotsFree: 2},
	})
	var urls []string
	for _, replica := range got {
		urls = append(urls, replica.URL)
	}
	want := []string{"http://b", "http://e", "http://c"}
	if !reflect.DeepEqual(urls, want) {
		t.Fatalf("got %v, want %v", urls, want)
	}
}

func TestNoEligibleReplicaMeansAnEmptyRanking(t *testing.T) {
	if got := Rank([]Replica{{URL: "x", Healthy: false, SlotsFree: 3}}); len(got) != 0 {
		t.Fatalf("got %v", got)
	}
}

func TestTheGlobalLimit(t *testing.T) {
	if !HasRoom(3, 4) || HasRoom(4, 4) || HasRoom(5, 4) {
		t.Fatal("HasRoom is wrong at the boundary")
	}
}
