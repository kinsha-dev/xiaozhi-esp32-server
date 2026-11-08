// Vibi.app launcher: runs the Mac menu-bar app (python -m mac_app) from SERVER_DIR with PY, as a child of the signed bundle
// so macOS attributes Bluetooth/microphone access to Vibi.app.
// build: scripts/build_vibi_launcher.sh
#include <signal.h>
#include <spawn.h>
#include <stdlib.h>
#include <sys/wait.h>
#include <unistd.h>
extern char **environ;
static pid_t child;
static void fwd(int s) { if (child > 0) kill(child, s); }
int main(void) {
    if (chdir(SERVER_DIR) != 0) return 70;
    char *argv[] = {"python", "-m", "mac_app", NULL};
    signal(SIGTERM, fwd); signal(SIGINT, fwd);
    if (posix_spawn(&child, PY, NULL, NULL, argv, environ) != 0) return 71;
    int st = 0;
    while (waitpid(child, &st, 0) < 0) {}
    return WIFEXITED(st) ? WEXITSTATUS(st) : 1;
}
