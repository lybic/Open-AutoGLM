"""Lybic cloud sandbox client for Phone Agent."""

import asyncio
import base64
import threading
from dataclasses import dataclass
from typing import Any, Optional

from lybic import LybicClient
from lybic.authentication import LybicAuth
from lybic.dto import (
    CreateSandboxDto,
    ExecuteSandboxActionDto,
    SandboxProcessRequestDto,
)
from lybic.action import (
    TouchTapAction,
    TouchSwipeAction,
    TouchLongPressAction,
    KeyboardTypeAction,
    AndroidBackAction,
    AndroidHomeAction,
    OsStartAppByNameAction,
    WaitAction,
    FinishedAction,
    PixelLength,
)
from phone_agent.config.apps import APP_PACKAGES


@dataclass
class Screenshot:
    """Represents a captured screenshot."""

    base64_data: str
    width: int
    height: int
    is_sensitive: bool = False


@dataclass
class LybicConfig:
    """Configuration for Lybic client."""

    org_id: Optional[str] = None
    api_key: Optional[str] = None
    endpoint: Optional[str] = None
    sandbox_id: Optional[str] = None
    sandbox_shape: str = "guangzhou-4c6g-android-12"
    sandbox_max_life_seconds: int = 3600


class LybicPhoneClient:
    """
    Lybic cloud sandbox client for phone automation.

    Replaces local ADB connection with cloud-based Android sandbox.
    """

    def __init__(self, config: LybicConfig):
        self.config = config
        self.sandbox_id: Optional[str] = config.sandbox_id
        self._client: Optional[LybicClient] = None
        self._screen_width: int = 1080
        self._screen_height: int = 2400
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._initialized = False

    def _ensure_loop(self):
        """Ensure we have a dedicated event loop in a separate thread."""
        if self._loop is None or not self._loop.is_running():
            self._loop = asyncio.new_event_loop()
            
            def run_loop():
                asyncio.set_event_loop(self._loop)
                self._loop.run_forever()
            
            self._loop_thread = threading.Thread(target=run_loop, daemon=True)
            self._loop_thread.start()

    def _run_async(self, coro):
        """Run an async coroutine in the dedicated loop."""
        self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=30)  # 30 second timeout

    async def _ensure_client(self) -> LybicClient:
        """Ensure client is initialized."""
        if self._client is None:
            auth = LybicAuth(
                org_id=self.config.org_id,
                api_key=self.config.api_key,
                endpoint=self.config.endpoint,
            ) if self.config.org_id or self.config.api_key or self.config.endpoint else None
            self._client = LybicClient(auth=auth)
            # Enter the context manager once
            await self._client.__aenter__()
            self._initialized = True
        return self._client

    async def _ensure_sandbox(self) -> str:
        """Ensure sandbox is created and return sandbox ID."""
        if self.sandbox_id:
            return self.sandbox_id

        client = await self._ensure_client()
        sandbox = await client.sandbox.create(
            CreateSandboxDto(
                name="phone-agent-sandbox",
                shape=self.config.sandbox_shape,
                maxLifeSeconds=self.config.sandbox_max_life_seconds,
            )
        )
        self.sandbox_id = sandbox.id
        print(f"Created sandbox: {self.sandbox_id}")
        return self.sandbox_id

    def get_screenshot_sync(self) -> Screenshot:
        """Get screenshot from sandbox (synchronous wrapper)."""
        return self._run_async(self._get_screenshot_async())

    async def _get_screenshot_async(self) -> Screenshot:
        """Get screenshot from sandbox."""
        sandbox_id = await self._ensure_sandbox()
        client = await self._ensure_client()

        screenshot_url, img, base64_str = await client.sandbox.get_screenshot(sandbox_id)

        if img:
            self._screen_width, self._screen_height = img.size

        return Screenshot(
            base64_data=base64_str,
            width=self._screen_width,
            height=self._screen_height,
            is_sensitive=False,
        )

    def execute_action_sync(self, action: Any) -> None:
        """Execute an action on the sandbox (synchronous wrapper)."""
        return self._run_async(self._execute_action_async(action))

    async def _execute_action_async(self, action: Any) -> None:
        """Execute an action on the sandbox."""
        sandbox_id = await self._ensure_sandbox()
        client = await self._ensure_client()

        action_dto = ExecuteSandboxActionDto(
            action=action,
            includeScreenShot=False,
        )
        await client.sandbox.execute_sandbox_action(sandbox_id, action_dto)

    async def close(self) -> None:
        """Close the client."""
        if self._client and self._initialized:
            await self._client.__aexit__(None, None, None)
            self._client = None
            self._initialized = False
        
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._loop_thread:
                self._loop_thread.join(timeout=2)

    def get_current_app(self) -> str:
        """
        Get current app name from lybic sandbox (synchronous wrapper).
        
        Uses execute_process to run 'dumpsys window' and parse the output.
        """
        return self._run_async(self._get_current_app_async())

    async def _get_current_app_async(self) -> str:
        """Get current app name from lybic sandbox (async implementation)."""
        try:
            sandbox_id = await self._ensure_sandbox()
            client = await self._ensure_client()

            result = await client.sandbox.execute_process(
                sandbox_id,
                SandboxProcessRequestDto(
                    executable="dumpsys",
                    args=['window']
                )
            )
            
            # Decode base64 stdout
            output = base64.b64decode(result.stdoutBase64).decode('utf-8', errors='ignore')
            
            # Parse window focus info (same logic as ADB version)
            for line in output.split("\n"):
                if "mCurrentFocus" in line or "mFocusedApp" in line:
                    for app_name, package in APP_PACKAGES.items():
                        if package in line:
                            return app_name
            
            return "System Home"
            
        except Exception as e:
            print(f"Warning: Failed to get current app from lybic: {e}")
            return "System Home"

    @property
    def screen_width(self) -> int:
        """Get screen width."""
        return self._screen_width

    @property
    def screen_height(self) -> int:
        """Get screen height."""
        return self._screen_height


def convert_action_to_lybic(action: dict[str, Any], screen_width: int, screen_height: int) -> Any:
    """
    Convert phone_agent action dict to lybic action object.

    Args:
        action: Action dictionary from the AI model.
        screen_width: Screen width in pixels.
        screen_height: Screen height in pixels.

    Returns:
        Lybic action object.
    """
    action_type = action.get("_metadata")

    if action_type == "finish":
        return FinishedAction(message=action.get("message"))

    action_name = action.get("action")

    def convert_relative_to_absolute(element: list[int]) -> tuple[int, int]:
        """Convert relative coordinates (0-1000) to absolute pixels."""
        x = int(element[0] / 1000 * screen_width)
        y = int(element[1] / 1000 * screen_height)
        return x, y

    if action_name == "Tap":
        element = action.get("element", [500, 500])
        x, y = convert_relative_to_absolute(element)
        return TouchTapAction(
            x=PixelLength(value=x),
            y=PixelLength(value=y),
        )

    elif action_name == "Double Tap":
        element = action.get("element", [500, 500])
        x, y = convert_relative_to_absolute(element)
        return TouchTapAction(
            x=PixelLength(value=x),
            y=PixelLength(value=y),
        )

    elif action_name == "Long Press":
        element = action.get("element", [500, 500])
        x, y = convert_relative_to_absolute(element)
        return TouchLongPressAction(
            x=PixelLength(value=x),
            y=PixelLength(value=y),
            duration=3000,
        )

    elif action_name == "Swipe":
        start = action.get("start", [500, 500])
        end = action.get("end", [500, 500])
        start_x, start_y = convert_relative_to_absolute(start)
        end_x, end_y = convert_relative_to_absolute(end)

        dx = end_x - start_x
        dy = end_y - start_y

        if abs(dx) > abs(dy):
            direction = "right" if dx > 0 else "left"
            distance = abs(dx)
        else:
            direction = "down" if dy > 0 else "up"
            distance = abs(dy)

        return TouchSwipeAction(
            x=PixelLength(value=start_x),
            y=PixelLength(value=start_y),
            direction=direction,
            distance=PixelLength(value=distance),
        )

    elif action_name in ("Type", "Type_Name"):
        text = action.get("text", "")
        return KeyboardTypeAction(content=text)

    elif action_name == "Back":
        return AndroidBackAction()

    elif action_name == "Home":
        return AndroidHomeAction()

    elif action_name == "Launch":
        app_name = action.get("app", "")
        return OsStartAppByNameAction(name=app_name)

    elif action_name == "Wait":
        duration_str = action.get("duration", "1 seconds")
        try:
            duration = float(duration_str.replace("seconds", "").strip())
        except ValueError:
            duration = 1.0
        return WaitAction(duration=int(duration * 1000))

    else:
        # Default: return a wait action for unknown actions
        return WaitAction(duration=1000)
