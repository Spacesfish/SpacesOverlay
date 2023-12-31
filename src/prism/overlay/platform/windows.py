import logging

import win32con  # type: ignore [import-untyped]
import win32gui  # type: ignore [import-untyped]

logger = logging.getLogger(__name__)


def set_windowstate(hwnd: int, fullscreen: bool) -> None:
    """Set a window to be borderless fullscreen/regular maximized"""
    style_BL = (
        win32con.WS_DLGFRAME
        | win32con.WS_CLIPSIBLINGS
        | win32con.WS_CLIPCHILDREN
        | win32con.WS_VISIBLE
    )
    style_Normal = (
        win32con.WS_DLGFRAME
        | win32con.WS_CLIPSIBLINGS
        | win32con.WS_CLIPCHILDREN
        | win32con.WS_VISIBLE
        | win32con.WS_BORDER
        | win32con.WS_DLGFRAME
        | win32con.WS_SYSMENU
        | win32con.WS_THICKFRAME
        | win32con.WS_MINIMIZEBOX
        | win32con.WS_MAXIMIZEBOX
    )
    if fullscreen:
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, style_BL)
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
    else:
        win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, style_Normal)
        win32gui.MoveWindow(hwnd, 100, 100, 1000, 600, False)
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)


def toggle_fullscreen() -> None:
    """Toggle minecraft maximized/borderless fullscreen"""
    hwnd = win32gui.FindWindow("LWJGL", None)

    if hwnd is None or hwnd == 0:
        logger.info(
            f"Could not find Minecraft window: {hwnd=}. Aborting toggle fullscreen."
        )
        return

    style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
    is_maximized = bool(style & win32con.WS_BORDER)

    try:
        # If we are mazimized we want borderless fullscreen next
        set_windowstate(hwnd, fullscreen=is_maximized)
    except Exception:
        logger.exception(
            f"Failed setting minecraft windowstate {hwnd=} {is_maximized=}"
        )
