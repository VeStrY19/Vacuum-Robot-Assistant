"""
robot_control.py
-----------------
Couche d'abstraction au-dessus de python-miio pour piloter le Roborock S5
(firmware Valetudo). Expose des actions simples que le module d'intention
vocale peut appeler sans connaître les détails de l'API miio.
"""

import difflib
import json
import logging
from pathlib import Path

import requests
from miio import RoborockVacuum

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("robot_control")

CONFIG_PATH = Path(__file__).parent / "rooms_config.json"


class RobotController:
    def __init__(self, ip: str, token: str, rooms_config_path: Path = CONFIG_PATH):
        self.vac = RoborockVacuum(ip, token)
        # API REST de Valetudo, utilisée pour le déplacement vers un point (goto)
        self.valetudo_url = f"http://{ip}/api/v2"
        self.rooms = self._load_rooms(rooms_config_path)

    @staticmethod
    def _load_rooms(path: Path) -> dict:
        """Charge le mapping nom de pièce (FR) -> segment_id Valetudo."""
        if not path.exists():
            logger.warning("Aucun fichier rooms_config.json trouvé (%s)", path)
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    # --- Commandes globales déjà existantes ---
    def start(self):
        logger.info("Commande: start")
        return self.vac.start()

    def stop(self):
        logger.info("Commande: stop")
        return self.vac.stop()

    def pause(self):
        logger.info("Commande: pause")
        return self.vac.pause()

    def home(self):
        logger.info("Commande: home")
        return self.vac.home()

    def status(self):
        return self.vac.status()

    # --- Navigation vers un point précis (x, y) ---
    def goto_point(self, x: int, y: int):
        """
        Envoie le robot vers un point de la carte via l'API REST de Valetudo.
        Les coordonnées sont celles de Valetudo (en cm, repère de la carte),
        pas celles du protocole miio brut : on n'utilise donc pas vac.goto().
        """
        logger.info("Commande: goto_point x=%s y=%s", x, y)
        resp = requests.put(
            f"{self.valetudo_url}/robot/capabilities/GoToLocationCapability",
            json={"action": "goto", "coordinates": {"x": int(x), "y": int(y)}},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.status_code

    # --- Navigation vers une pièce nommée (segment) ---
    def list_rooms(self) -> dict:
        """Retourne le mapping des pièces configurées."""
        return self.rooms

    def discover_segments(self):
        """
        Utilitaire à lancer une fois manuellement pour récupérer les segment_id
        réels de ta carte (à copier ensuite dans rooms_config.json).
        """
        return self.vac.get_room_mapping()

    def resolve_location(self, name: str):
        """
        Retrouve le nom exact présent dans rooms_config.json. Tolère les petites
        erreurs de transcription vocale (ex: "Stephens" -> "stevens").
        Retourne None si rien ne ressemble assez.
        """
        key = name.strip().lower()
        if key in self.rooms:
            return key
        matches = difflib.get_close_matches(key, list(self.rooms.keys()), n=1, cutoff=0.6)
        return matches[0] if matches else None

    def goto_location(self, name: str):
        """
        Déplace le robot, SANS nettoyer, vers un lieu ou une personne enregistré(e)
        dans rooms_config.json sous la forme {"x": ..., "y": ...} (cm, repère
        Valetudo). Retourne le nom résolu, ou None si le lieu est inconnu.
        """
        key = self.resolve_location(name)
        if key is None:
            logger.warning("Lieu inconnu: %s (connus: %s)", name, list(self.rooms.keys()))
            return None
        point = self.rooms[key]
        logger.info("Commande: goto_location %s -> point %s", key, point)
        self.goto_point(point["x"], point["y"])
        return key

    # Ancien nom, conservé pour compatibilité avec les anciens scripts
    goto_room = goto_location
