// This fixture is copied into an isolated driver checkout by run_local.py.
package nfs

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sync/atomic"
	"syscall"
	"testing"
	"time"

	"github.com/container-storage-interface/spec/lib/go/csi"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	mount "k8s.io/mount-utils"
)

// Only the isolated checkout calls these wrappers. Normally they delegate
// directly to the original filesystem functions; the overlap test adds gates.
var adversarialRemoveArchive = os.RemoveAll
var adversarialRenameArchive = os.Rename

type archiveFixture struct {
	t                      *testing.T
	root, source, archived string
	vol                    *nfsVolume
	cs                     *ControllerServer
}

func newArchiveFixture(t *testing.T, replace bool) *archiveFixture {
	t.Helper()
	f := &archiveFixture{t: t, root: t.TempDir()}
	f.vol = &nfsVolume{server: testServer, baseDir: testBaseDir, subDir: "namespace/volume", uuid: "pv", onDelete: archive}
	f.cs = f.controller(replace)
	f.source = filepath.Join(f.root, "pv", f.vol.subDir)
	f.archived = filepath.Join(f.root, "pv", "archived-"+f.vol.subDir)
	f.must(os.MkdirAll(filepath.Dir(f.source), 0755))
	f.must(os.MkdirAll(filepath.Dir(f.archived), 0755))
	return f
}

func (f *archiveFixture) must(err error) {
	f.t.Helper()
	if err != nil {
		f.t.Fatal(err)
	}
}

func (f *archiveFixture) controller(replace bool) *ControllerServer {
	cs := initTestController(f.t)
	cs.Driver.workingMountDir = f.root
	cs.Driver.removeArchivedVolumePath = replace
	return cs
}

func (f *archiveFixture) data(path, value string) {
	f.t.Helper()
	f.must(os.MkdirAll(path, 0755))
	f.must(os.WriteFile(filepath.Join(path, "data"), []byte(value), 0600))
}

func (f *archiveFixture) check(path, want string) {
	f.t.Helper()
	b, err := os.ReadFile(filepath.Join(path, "data"))
	if err != nil || string(b) != want {
		f.t.Errorf("payload mismatch: want %q, got %q, error %v", want, b, err)
	}
}

func (f *archiveFixture) delete(cs *ControllerServer) error {
	_, err := cs.DeleteVolume(context.Background(), &csi.DeleteVolumeRequest{VolumeId: getVolumeIDFromNfsVol(f.vol)})
	return err
}

func TestAdversarialFreshControllerRetries(t *testing.T) {
	for _, replace := range []bool{false, true} {
		t.Run(fmt.Sprint(replace), func(t *testing.T) {
			f := newArchiveFixture(t, replace)
			f.data(f.source, "original payload")
			f.must(f.delete(f.cs))
			for i := 0; i < 5; i++ {
				if err := f.delete(f.controller(replace)); err != nil {
					t.Errorf("fresh controller retry %d: %v", i, err)
				}
				f.check(f.archived, "original payload")
			}
		})
	}
}

func TestAdversarialMissingSourcePreservesArchive(t *testing.T) {
	f := newArchiveFixture(t, true)
	f.data(f.archived, "completed archive")
	if err := f.delete(f.cs); err != nil {
		t.Errorf("missing source: %v", err)
	}
	f.check(f.archived, "completed archive")
}

func TestAdversarialPermissionDenied(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("requires an unprivileged process to enforce directory search permissions")
	}
	f := newArchiveFixture(t, true)
	f.data(f.source, "current")
	f.data(f.archived, "previous")
	parent := filepath.Dir(f.source)
	f.must(os.Chmod(parent, 0))
	defer os.Chmod(parent, 0755)
	if _, err := os.Lstat(f.source); !os.IsPermission(err) {
		t.Fatalf("fixture did not deny access: %v", err)
	}
	if err := f.delete(f.cs); status.Code(err) != codes.Internal {
		t.Errorf("want Internal on permission failure, got %v", err)
	}
	f.check(f.archived, "previous")
	f.must(os.Chmod(parent, 0755))
	f.check(f.source, "current")
}

func TestAdversarialSymlinkLoop(t *testing.T) {
	f := newArchiveFixture(t, true)
	f.data(f.archived, "previous")
	parent := filepath.Dir(f.source)
	f.must(os.Remove(parent))
	f.must(os.Symlink(filepath.Base(parent), parent))
	if _, err := os.Lstat(f.source); !errors.Is(err, syscall.ELOOP) {
		t.Fatalf("fixture did not cause ELOOP: %v", err)
	}
	if err := f.delete(f.cs); status.Code(err) != codes.Internal {
		t.Errorf("want Internal on symlink loop, got %v", err)
	}
	f.check(f.archived, "previous")
}

func TestAdversarialDanglingSymlink(t *testing.T) {
	f := newArchiveFixture(t, true)
	f.must(os.Symlink("does-not-exist", f.source))
	f.must(f.delete(f.cs))
	target, err := os.Readlink(f.archived)
	if err != nil || target != "does-not-exist" {
		t.Errorf("symlink not archived: %q %v", target, err)
	}
	if _, err := os.Lstat(f.source); !os.IsNotExist(err) {
		t.Errorf("source link still exists: %v", err)
	}
}

func TestAdversarialSymlinkTargetUntouched(t *testing.T) {
	f := newArchiveFixture(t, true)
	target := t.TempDir()
	f.data(target, "outside sentinel")
	f.must(os.Symlink(target, f.source))
	f.must(f.delete(f.cs))
	f.check(target, "outside sentinel")
	actual, err := os.Readlink(f.archived)
	if err != nil || actual != target {
		t.Errorf("symlink not archived: %q %v", actual, err)
	}
}

func TestAdversarialMountError(t *testing.T) {
	f := newArchiveFixture(t, true)
	f.data(f.archived, "previous")
	mounter := &mount.FakeMounter{MountCheckErrors: map[string]error{filepath.Join(f.root, "pv"): syscall.EIO}}
	f.cs.Driver.ns = NewNodeServer(f.cs.Driver, mounter)
	if err := f.delete(f.cs); status.Code(err) != codes.Internal {
		t.Errorf("want Internal on mount failure, got %v", err)
	}
	f.check(f.archived, "previous")
}

func TestAdversarialSameControllerLock(t *testing.T) {
	f := newArchiveFixture(t, true)
	f.data(f.source, "current")
	f.data(f.archived, "previous")
	id := getVolumeIDFromNfsVol(f.vol)
	if !f.cs.Driver.volumeLocks.TryAcquire(id) {
		t.Fatal("could not acquire fixture lock")
	}
	defer f.cs.Driver.volumeLocks.Release(id)
	if err := f.delete(f.cs); status.Code(err) != codes.Aborted {
		t.Errorf("want Aborted for overlapping call, got %v", err)
	}
	f.check(f.source, "current")
	f.check(f.archived, "previous")
}

func TestAdversarialTwoControllers(t *testing.T) {
	for _, replace := range []bool{false, true} {
		t.Run(fmt.Sprint(replace), func(t *testing.T) {
			f := newArchiveFixture(t, replace)
			f.data(f.source, "only copy")
			second := f.controller(replace)
			bothArrived, firstRenamed := make(chan struct{}), make(chan struct{})
			var arrivals, renames atomic.Int32
			wait := func(ch <-chan struct{}) error {
				select {
				case <-ch:
					return nil
				case <-time.After(5 * time.Second):
					return fmt.Errorf("scheduling barrier timed out")
				}
			}
			gate := func() error {
				if arrivals.Add(1) == 1 {
					return wait(bothArrived)
				}
				close(bothArrived)
				return wait(firstRenamed)
			}
			adversarialRemoveArchive = func(path string) error {
				if err := gate(); err != nil {
					return err
				}
				return os.RemoveAll(path)
			}
			adversarialRenameArchive = func(source, target string) error {
				if !replace {
					if err := gate(); err != nil {
						return err
					}
				}
				err := os.Rename(source, target)
				if renames.Add(1) == 1 {
					close(firstRenamed)
				}
				return err
			}
			defer func() { adversarialRemoveArchive = os.RemoveAll; adversarialRenameArchive = os.Rename }()
			results := make(chan error, 2)
			go func() { results <- f.delete(f.cs) }()
			go func() { results <- f.delete(second) }()
			a, b := <-results, <-results
			t.Logf("independent controller outcomes: %s, %s", status.Code(a), status.Code(b))
			if arrivals.Load() != 2 || renames.Load() != 2 {
				t.Fatalf("schedule not exercised: arrivals=%d renames=%d", arrivals.Load(), renames.Load())
			}
			if (a == nil) == (b == nil) {
				t.Errorf("expected one rename success and one missing-source error: %v; %v", a, b)
			}
			f.check(f.archived, "only copy")
		})
	}
}
