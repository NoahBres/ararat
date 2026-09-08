/*
 * rtk-api launcher -- a tiny "responsible process" for macOS TCC.
 *
 * macOS attributes privacy permissions (Full Disk Access, "access data from
 * other apps", Automation, ...) to the *responsible process* of a launchd job:
 * the job's main executable. If that executable is a generic tool (python,
 * uv, sh) the prompts and the Privacy & Security lists show that generic
 * name, and the grant moves whenever the tool's path changes.
 *
 * This launcher is built ONCE into ~/Applications/rtk-api.app and never
 * needs rebuilding. launchd runs it as the job's program; it spawns the real
 * command (argv[1..]) as a child, forwards SIGTERM/SIGINT, and exits with the
 * child's status. Everything the child (and grandchildren) touch is then
 * attributed to "rtk-api", so Noah grants permissions to one named app.
 *
 * Usage: rtk-api <command> [args...]
 */
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>
#include <errno.h>

static int errno_is_eintr(void) { return errno == EINTR; }

extern char **environ;

static pid_t child = 0;

static void forward(int sig) {
    if (child > 0) kill(child, sig);
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s <command> [args...]\n", argv[0]);
        return 64;
    }

    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = forward;
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGHUP, &sa, NULL);

    int rc = posix_spawnp(&child, argv[1], NULL, NULL, &argv[1], environ);
    if (rc != 0) {
        fprintf(stderr, "rtk-api launcher: failed to spawn %s: %s\n", argv[1], strerror(rc));
        return 127;
    }

    int status = 0;
    for (;;) {
        pid_t w = waitpid(child, &status, 0);
        if (w == child) break;
        if (w < 0 && errno_is_eintr()) continue;
        return 1;
    }
    if (WIFEXITED(status)) return WEXITSTATUS(status);
    if (WIFSIGNALED(status)) return 128 + WTERMSIG(status);
    return 1;
}
