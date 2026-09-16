// Package capacity finds agent replicas and picks one for the next run.
//
// Replicas are discovered by resolving the compose service name: Docker's embedded DNS returns
// one address per container, so `docker compose up --scale agent=N` needs no configuration.
// AGENT_URLS overrides discovery for non-compose setups and tests.
package capacity

import (
	"cmp"
	"context"
	"fmt"
	"net"
	"slices"
)

// Replica is one agent as seen by the dispatcher at a point in time.
type Replica struct {
	URL       string
	AgentID   string
	Healthy   bool
	SlotsFree int
}

// Resolver is net.Resolver's LookupHost, abstracted for tests.
type Resolver interface {
	LookupHost(ctx context.Context, host string) ([]string, error)
}

// Discover returns candidate base URLs.
func Discover(ctx context.Context, static []string, resolver Resolver, host string, port int) ([]string, error) {
	if len(static) > 0 {
		return slices.Clone(static), nil
	}
	addresses, err := resolver.LookupHost(ctx, host)
	if err != nil {
		return nil, fmt.Errorf("resolve agent service %q: %w", host, err)
	}
	slices.Sort(addresses)
	urls := make([]string, 0, len(addresses))
	for _, address := range addresses {
		urls = append(urls, "http://"+net.JoinHostPort(address, fmt.Sprint(port)))
	}
	return urls, nil
}

// Rank orders the replicas that can take work: healthy ones with a free slot, most free slots
// first, URL as a stable tie-breaker so behaviour is reproducible in tests and logs.
func Rank(replicas []Replica) []Replica {
	eligible := make([]Replica, 0, len(replicas))
	for _, replica := range replicas {
		if replica.Healthy && replica.SlotsFree > 0 {
			eligible = append(eligible, replica)
		}
	}
	slices.SortStableFunc(eligible, func(a, b Replica) int {
		if byFree := cmp.Compare(b.SlotsFree, a.SlotsFree); byFree != 0 {
			return byFree
		}
		return cmp.Compare(a.URL, b.URL)
	})
	return eligible
}

// HasRoom applies the global limit across all replicas (config: max_concurrent_runs).
func HasRoom(active, maxConcurrent int) bool { return active < maxConcurrent }
