"""Input helpers built on prompt_toolkit."""

from __future__ import annotations

from prompt_toolkit import PromptSession


class InputSession:
    """Async prompt wrapper."""

    def __init__(self) -> None:
        try:
            self._session = PromptSession()
        except Exception:
            # Headless CI and service processes do not have a Windows console.
            # Keep mode/state operations available; prompt calls still fail
            # clearly if a caller attempts interactive input in that context.
            self._session = None
        self._prompt = "> "

    def set_modes(self, *, vim_enabled: bool, voice_enabled: bool) -> None:
        """Update prompt decorations for active modes."""
        parts: list[str] = []
        if vim_enabled:
            parts.append("[vim]")
        if voice_enabled:
            parts.append("[voice]")
        prefix = "".join(parts)
        self._prompt = f"{prefix}> " if prefix else "> "

    async def prompt(self) -> str:
        """Prompt the user for one line of input."""
        if self._session is None:
            raise RuntimeError("interactive input requires a console")
        return await self._session.prompt_async(self._prompt)

    async def ask(self, question: str) -> str:
        """Prompt the user for an ad-hoc answer."""
        prompt = f"[question] {question}\n> "
        if self._session is None:
            raise RuntimeError("interactive input requires a console")
        return await self._session.prompt_async(prompt)
