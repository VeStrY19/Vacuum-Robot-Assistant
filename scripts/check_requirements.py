# -*- coding: utf-8 -*-
import json
import sys
import io

def validate_project():
    file_path = r"..\ephemeris_project_Test_req_10_21_2026, 11_32_33 AM.json"
    try:
        # io.open gère l'encodage de manière compatible Python 2 et Python 3
        with io.open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        requirements = data.get("data", {}).get("requirements", {}).get("items", [])
        print("Succes : {} exigence(s) trouvee(s) dans le fichier.".format(len(requirements)))
        for req in requirements:
            print(" - [{}] {}".format(req.get('uuid'), req.get('name')))
        
    except Exception as e:
        print("Erreur lors de la lecture du projet Ephemeris : {}".format(e))
        sys.exit(1)

if __name__ == "__main__":
    validate_project()