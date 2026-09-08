/*
 * trash-reminder launcher -- a tiny "responsible process" for macOS TCC.
 *
 * Same pattern as rtk-api/launcher (see ../../rtk-api/launcher/main.c):
 * macOS attributes privacy permissions (Full Disk Access, "access data from
 * other apps", Automation, ...) to the *responsible process* of a launchd
 * job: the job's main executable. Running the trash jobs under bare
 * /usr/bin/python3 would put the grant on system-wide python; this launcher
 * lets Noah grant Full Disk Access to "trash-reminder" instead, scoped to
 * just tools/trash-reminder.py and tools/trash-skip-watcher.py.
 *
 * Built ONCE into ~/Applications/trash-reminder.app (tools/trash-launcher/
 * build.sh, run on rtk) and never rebuilt: rebuilding changes the ad-hoc
 * signature and invalidates the TCC grant. launchd runs it as the job's
 * program; it spawns the real command (argv[1..]) as a child, forwards
 * SIGTERM/SIGINT, and exits with the child's status.
 *
 * Usage: trash-reminder <command> [args...]
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
        fprintf(stderr, "trash-reminder launcher: failed to spawn %s: %s\n", argv[1], strerror(rc));
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
