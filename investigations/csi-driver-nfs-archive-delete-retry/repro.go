// Exercise archive deletion retries against an isolated CSI controller.
package main

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/container-storage-interface/spec/lib/go/csi"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/status"
)

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run() error {
	runID := os.Getenv("RUN_ID")
	if !regexp.MustCompile(`^nfs-archive-[a-f0-9]{12}$`).MatchString(runID) {
		return fmt.Errorf("RUN_ID must be nfs-archive- followed by 12 hex characters")
	}
	server, share := os.Getenv("NFS_SERVER"), os.Getenv("NFS_SHARE")
	if server == "" || !strings.HasPrefix(share, "/") {
		return fmt.Errorf("set NFS_SERVER and an absolute NFS_SHARE")
	}
	conn, err := grpc.NewClient("unix:///csi/csi.sock", grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return err
	}
	defer conn.Close()
	client := csi.NewControllerClient(conn)
	create := func(name, policy string) (string, error) {
		ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
		defer cancel()
		response, err := client.CreateVolume(ctx, &csi.CreateVolumeRequest{
			Name:       runID + "-" + strings.ReplaceAll(name, "/", "-"),
			Parameters: map[string]string{"server": server, "share": share, "subDir": runID + "/" + name, "onDelete": policy},
			VolumeCapabilities: []*csi.VolumeCapability{{
				AccessType: &csi.VolumeCapability_Mount{Mount: &csi.VolumeCapability_MountVolume{MountFlags: []string{"nfsvers=4.1"}}},
				AccessMode: &csi.VolumeCapability_AccessMode{Mode: csi.VolumeCapability_AccessMode_MULTI_NODE_MULTI_WRITER},
			}},
		})
		if err != nil {
			return "", err
		}
		return response.Volume.VolumeId, nil
	}
	deleteVolume := func(id string) error {
		ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
		defer cancel()
		_, err := client.DeleteVolume(ctx, &csi.DeleteVolumeRequest{VolumeId: id, Secrets: map[string]string{"mountOptions": "nfsvers=4.1"}})
		return err
	}
	// These roots are unique to this run and contain only synthetic test data.
	sourceRoot := filepath.Join("/export", runID)
	archiveRoot := filepath.Join("/export", "archived-"+runID)
	for _, path := range []string{sourceRoot, archiveRoot} {
		if _, err := os.Lstat(path); !os.IsNotExist(err) {
			return fmt.Errorf("test path already exists or cannot be checked: %s", path)
		}
	}
	archiveID, err := create("nested/volume", "archive")
	if err != nil {
		return fmt.Errorf("create archive volume: %w", err)
	}
	deleteID, err := create("delete-control", "delete")
	if err != nil {
		return fmt.Errorf("create delete control: %w", err)
	}
	payload := make([]byte, 1024*1024)
	if _, err := rand.Read(payload); err != nil {
		return err
	}
	want := sha256.Sum256(payload)
	sourceFile := filepath.Join(sourceRoot, "nested/volume/data")
	archiveFile := filepath.Join(archiveRoot, "nested/volume/data")
	if err := os.WriteFile(sourceFile, payload, 0600); err != nil {
		return err
	}
	matches := func() bool {
		data, err := os.ReadFile(archiveFile)
		return err == nil && sha256.Sum256(data) == want
	}
	if err := deleteVolume(archiveID); err != nil {
		return fmt.Errorf("first archive delete: %w", err)
	}
	if !matches() {
		return fmt.Errorf("first archive delete did not preserve the payload")
	}
	if err := deleteVolume(archiveID); err != nil || !matches() {
		return fmt.Errorf("cached retry failed or changed the archive: %v", err)
	}
	if err := os.WriteFile(archiveFile, []byte("deliberate corruption"), 0600); err != nil {
		return err
	}
	if matches() {
		return fmt.Errorf("checksum verification missed deliberate corruption")
	}
	if err := os.WriteFile(archiveFile, payload, 0600); err != nil {
		return err
	}
	if err := deleteVolume(deleteID); err != nil {
		return fmt.Errorf("first normal delete: %w", err)
	}
	waitSeconds := 70
	retryMode := "cache-expiry"
	if endpoint := os.Getenv("RETRY_ENDPOINT"); endpoint != "" {
		freshConn, err := grpc.NewClient(endpoint, grpc.WithTransportCredentials(insecure.NewCredentials()))
		if err != nil {
			return err
		}
		defer freshConn.Close()
		client = csi.NewControllerClient(freshConn)
		waitSeconds, retryMode = 0, "separate-process"
		fmt.Fprintln(os.Stderr, "Retrying through a separate controller process with an empty cache.")
	} else {
		fmt.Fprintln(os.Stderr, "First deletes, cached retry, and checksum negative control passed; waiting for cache expiry.")
		time.Sleep(35 * time.Second)
		time.Sleep(35 * time.Second)
	}
	archiveErr := deleteVolume(archiveID)
	deleteErr := deleteVolume(deleteID)
	preserved := matches()
	_, sourceErr := os.Lstat(filepath.Dir(sourceFile))
	_, deletedErr := os.Lstat(filepath.Join(sourceRoot, "delete-control"))
	result := map[string]interface{}{
		"archive_retry_code":               status.Code(archiveErr).String(),
		"archive_preserved":                preserved,
		"source_absent":                    os.IsNotExist(sourceErr),
		"normal_delete_retry_code":         status.Code(deleteErr).String(),
		"normal_delete_absent":             os.IsNotExist(deletedErr),
		"cached_retry_passed":              true,
		"checksum_negative_control_passed": true,
		"cache_wait_seconds":               waitSeconds,
		"retry_mode":                       retryMode,
	}
	// Remove only this run's two validated roots, after recording the observations.
	for _, path := range []string{sourceRoot, archiveRoot} {
		if err := os.RemoveAll(path); err != nil {
			return fmt.Errorf("cleanup %s: %w", path, err)
		}
	}
	result["cleanup_passed"] = true
	if err := json.NewEncoder(os.Stdout).Encode(result); err != nil {
		return err
	}
	if archiveErr != nil || !preserved || !os.IsNotExist(sourceErr) || deleteErr != nil || !os.IsNotExist(deletedErr) {
		return fmt.Errorf("archive deletion retry violated the expected contract")
	}
	return nil
}
