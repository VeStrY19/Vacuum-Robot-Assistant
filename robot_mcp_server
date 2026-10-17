"""
robot_mcp_server.py
--------------------
Serveur MCP qui expose le contrôle du robot vacuum (via robot_control.py /
python-miio) comme des "tools" standardisés, appelables directement par un
LLM supportant le tool-calling (comme Qwen via Ollama).

python-miio reste la seule couche qui communique réellement avec le robot.
MCP ne fait qu'exposer ces fonctions de façon standardisée pour le LLM,
à la place de notre ancienne classification JSON faite à la main.

Lancement (généralement pas besoin de le lancer soi-même : le client MCP
dans nexo_mcp_assistant.py le démarre automatiquement en sous-processus) :
    python robot_mcp_server.py
"""

import os

from fastmcp import FastMCP
from robot_control import RobotController

# --- Config robot ---
ROBOT_IP = os.environ["ROBOT_IP"]
ROBOT_TOKEN = os.environ["ROBOT_TOKEN"]

mcp = FastMCP("Robot Vacuum")
robot = RobotController(ROBOT_IP, ROBOT_TOKEN)


@mcp.tool
def start_cleaning() -> str:
    """Start the vacuum's full cleaning cycle."""
    robot.start()
    return "The vacuum has started cleaning."


@mcp.tool
def stop_cleaning() -> str:
    """Stop the vacuum immediately."""
    robot.stop()
    return "The vacuum has stopped."


@mcp.tool
def pause_cleaning() -> str:
    """Pause the vacuum's current cleaning cycle."""
    robot.pause()
    return "The vacuum has been paused."


@mcp.tool
def return_to_dock() -> str:
    """Send the vacuum back to its charging dock."""
    robot.home()
    return "The vacuum is heading back to its dock."


def go_to_location(location_name: str) -> str:
    key = robot.goto_location(location_name)
    if key is None:
        known = ", ".join(robot.list_rooms().keys())
        return f"Unknown location '{location_name}'. Known locations: {known}."
    return f"The vacuum is heading to {key}."


# La description est construite au démarrage à partir de rooms_config.json, pour
# que le LLM connaisse exactement les noms disponibles (pièces ou personnes).
_known_locations = ", ".join(robot.list_rooms().keys())
go_to_location.__doc__ = (
    "Move the vacuum silently (no cleaning) to a named place or to the spot "
    "where a person is. Use this when someone asks the robot to come to a "
    "person or a room in order to talk with them.\n\n"
    f"Known locations: {_known_locations}.\n\n"
    "Args:\n"
    "    location_name: One of the known locations, in lowercase, e.g. a "
    "person's first name or a room name."
)
mcp.tool(go_to_location)


@mcp.tool
def get_status() -> str:
    """Get the vacuum's current status: battery level, state, and any errors."""
    s = robot.status()
    return str(s)


if __name__ == "__main__":
    mcp.run()
