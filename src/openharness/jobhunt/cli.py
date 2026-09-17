"""job-hunt CLI subcommand entry (Phase 0 placeholder).

Concrete subcommands (resume / match / interview / track ...) are wired up
in Phase 5. For now this module only reserves the mount point so the main
app in ``openharness.cli`` can attach the sub-application.
"""

from __future__ import annotations

import typer

#求职工具子应用，挂载到主 CLI（见 openharness.cli 中的 app.add_typer）
jobhunt_app = typer.Typer(
    name="job-hunt",
    help="Job hunting toolkit: resumes, job matching, interview prep and tracking.",
    #不携带子命令时也执行回调，输出占位提示而不是报错
    invoke_without_command=True,
)


@jobhunt_app.callback(invoke_without_command=True)
def jobhunt_main() -> None:
    """Placeholder entry; full job-hunt subcommands arrive in Phase 5."""
    #Phase 0 占位输出，Phase 5 会替换为真正的子命令分发
    print("job-hunt: skeleton only for now - subcommands arrive in Phase 5.")
