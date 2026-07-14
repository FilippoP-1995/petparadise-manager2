"""Inventario iniziale importato da INVENTARIO URNE.pdf (13 luglio 2026)."""

_INVENTORY = """Standard Grande|Legno|1|20
Standard Piccola|Legno|12|20
Doppia Cornice Bianca M|Legno|3|80
Doppia Cornice Noce M|Legno|1|80
Doppia Cornice Mogano M|Legno|2|80
Doppia Cornice Nera M|Legno|4|80
Doppia Cornice Bianca L|Legno|1|100
Doppia Cornice Noce L|Legno|6|100
Doppia Cornice Mogano L|Legno|1|100
Doppia Cornice Nera L|Legno|2|100
Doppia Cornice Argento L|Legno|3|100
Doppia Cornice Bianca XL|Legno|1|110
Doppia Cornice Noce XL|Legno|1|110
Doppia Cornice Mogano XL|Legno|1|110
Doppia Cornice Nera XL|Legno|2|110
Cornice Bianca M|Legno|0|60
Cornice Noce M|Legno|0|60
Cornice Mogano M|Legno|0|60
Cornice Nera M|Legno|1|60
Cornice Bianca L|Legno|0|70
Cornice Noce L|Legno|1|70
Cornice Mogano L|Legno|0|70
Cornice Nera L|Legno|0|70
Cornice Argento L|Legno|1|70
Cornice Bianca XL|Legno|0|80
Cornice Noce XL|Legno|0|80
Cornice Mogano XL|Legno|0|80
Cornice Nera XL|Legno|0|80
Forever|Legno|5|70
Pet Quadro Foglia Argento|Legno|1|160
Pet Quadro Foglia Oro|Legno|1|160
Pet Orma|Legno|1|70
Statua Bau|Ceramica|0|100
Statua Miao|Ceramica|0|100
Eterna Primavera Verde|Ceramica|1|70
Eterna Primavera Bianca|Ceramica|1|70
Anima Bianca|Ceramica|3|70
Anima Grigia|Ceramica|2|70
Salto d’Amore Bianca|Ceramica|1|70
PLA Bunny|Ceramica|0|60
PLA French Bulldog Bianco|Ceramica|0|80
PLA French Bulldog Nero|Ceramica|0|80
PLA Felix|Ceramica|0|60
PLA Pet’s Home|Ceramica|1|80
Pet Smart Nero|Metallo|0|60
Pet Smart Verde|Metallo|1|60
Pet Smart Grigio|Metallo|0|60
Pet Smart Blu Notte|Metallo|0|60
Pet Smart Rosso|Metallo|1|60
Pet Smart Rosa|Metallo|0|60
Pet Smart Blu|Metallo|1|60
Pet Rosso S|Metallo|0|70
Pet Grigio S|Metallo|0|70
Pet Ruggine S|Metallo|0|70
Pet Bianco S|Metallo|0|70
Pet Rosa S|Metallo|2|70
Pet Blu S|Metallo|3|70
Pet Grigio G|Metallo|1|90
Pet Ruggine G|Metallo|0|90
Pet Bianco G|Metallo|0|90
Pet Rosso G|Metallo|0|90
Pet Blu G|Metallo|0|90
Pet Rosa G|Metallo|1|90
Pet Tiffany G|Metallo|2|90
Tulipet Rosso|Metallo|0|80
Tulipet Bianco|Metallo|1|80
Tulipet Ruggine|Metallo|0|80
Pet Cubo Cuore Nero|Metallo|0|90
Pet Kubo Bianco|Metallo|0|90
Pet Kubo Nero|Metallo|0|90
Pet Quadro Ruggine|Metallo|0|100
Pet Quadro Bianco|Metallo|0|100
Pet Quadro Nero|Metallo|0|100
Cuore Nero M|Ceramica|0|80
Cuore Bianco M|Ceramica|0|80
Cuore Nero L|Ceramica|1|90
Cuore Bianco L|Ceramica|0|90
Kintsugi Bianco|Ceramica|0|100
Kintsugi Nero|Ceramica|1|100
Kintsugi Bianco Drop S|Ceramica|1|70
Kintsugi Nero Drop S|Ceramica|0|70
Kintsugi Bianco Drop L|Ceramica|0|100
Kintsugi Nero Drop L|Ceramica|1|100
Ali di Pace|Ceramica|1|40
Rosazampa|Ceramica|1|40"""

DEFAULT_URNS = [
    (name, material, int(quantity), price)
    for name, material, quantity, price in
    (line.split("|") for line in _INVENTORY.splitlines())
]
