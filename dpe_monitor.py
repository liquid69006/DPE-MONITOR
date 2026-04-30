"""
DPE Prospector v5 — Surveillance des nouveaux DPE · Lyon 3e arrondissement
Email quotidien à 8h — avec ou sans nouveaux DPE
Source : API Open Data ADEME (data.ademe.fr)
"""

import os
import json
import math
import smtplib
import requests
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# ══════════════════════════════════════════════════════════════
#  CONFIGURATION
# ══════════════════════════════════════════════════════════════

# Zones géographiques définies par polygones GPS
ZONES = {
    "Part-Dieu": {
        "codes_postaux": ["69003"],
        "polygone": [
            (45.76355139150843,  4.840950020902966),
            (45.75642429562913,  4.8395441997801925),
            (45.74942708666637,  4.860100428637281),
            (45.763682155404695, 4.860287871453295),
        ]
    },
    "Dauphiné-Lacassagne": {
        "codes_postaux": ["69003"],
        "polygone": [
            (45.763666847297316, 4.860882903977199),
            (45.74943467306812,  4.860095318969854),
            (45.74553255509332,  4.871239646832663),
            (45.7506712028316,   4.87391743586096),
            (45.75278697910744,  4.869743235317657),
            (45.75449053288179,  4.87364178110758),
            (45.754847723046254, 4.87525633037356),
            (45.75490267517603,  4.87616205313347),
            (45.75833707592611,  4.876634604137479),
            (45.758707127819065, 4.873773912182969),
            (45.76058202973891,  4.874885840542078),
            (45.761682107375805, 4.871798156197514),
            (45.76371663644693,  4.870115856267233),
        ]
    },
    "Montchat": {
        "codes_postaux": ["69003"],
        "polygone": [
            (45.752807471686765, 4.8696556919393),
            (45.750648569367286, 4.873968991938966),
            (45.74554776311837,  4.8712261737505),
            (45.74309361037359,  4.878425233301357),
            (45.74276386559836,  4.878697742930143),
            (45.74164596308222,  4.884055610780678),
            (45.739037436828085, 4.892083206597164),
            (45.74349477437073,  4.89467820901416),
            (45.746809758539996, 4.89646416496484),
            (45.74891686986501,  4.896869278651252),
            (45.751589171037665, 4.8975505195815),
            (45.752963814591624, 4.8984527035141525),
            (45.75343915074612,  4.896685159480938),
            (45.75400441009205,  4.893941783846714),
            (45.75409433718764,  4.892339947067768),
            (45.75431055205931,  4.887625722940868),
            (45.75424631868333,  4.8851585260613035),
            (45.75438763201271,  4.883096391355906),
            (45.75499142129863,  4.878401352518125),
            (45.75499142129863,  4.877296637497693),
            (45.754901495648596, 4.875437033880047),
            (45.75461887123453,  4.874092963938381),
        ]
    },
}

EMAIL_EXPEDITEUR   = os.getenv("EMAIL_EXPEDITEUR", "")
EMAIL_DESTINATAIRE = os.getenv("EMAIL_DESTINATAIRE", "dauphine.lacassagne@century21.fr")
EMAIL_CC           = os.getenv("EMAIL_CC",           "ybufferne@century21.fr")
EMAIL_MOT_DE_PASSE = os.getenv("EMAIL_MOT_DE_PASSE", "")

CACHE_FILE = "dpe_cache.json"
JOURS_HISTORIQUE_INITIAL = 30

# ══════════════════════════════════════════════════════════════
#  API ADEME
# ══════════════════════════════════════════════════════════════

API_BASE = "https://data.ademe.fr/data-fair/api/v1/datasets/meg-83tjwtg8dyz4vv7h1dqe/lines"

CHAMPS = [
    "numero_dpe",
    "date_reception_dpe",
    "adresse_ban",
    "code_postal_ban",
    "nom_commune_ban",
    "_geopoint",
]

# ══════════════════════════════════════════════════════════════
#  GÉOGRAPHIE
# ══════════════════════════════════════════════════════════════

def point_dans_polygone(lat, lng, polygone) -> bool:
    """Ray casting algorithm — détermine si un point est dans un polygone."""
    n = len(polygone)
    dedans = False
    j = n - 1
    for i in range(n):
        lat_i, lng_i = polygone[i]
        lat_j, lng_j = polygone[j]
        if ((lng_i > lng) != (lng_j > lng)) and            (lat < (lat_j - lat_i) * (lng - lng_i) / (lng_j - lng_i) + lat_i):
            dedans = not dedans
        j = i
    return dedans

# ══════════════════════════════════════════════════════════════
#  CACHE
# ══════════════════════════════════════════════════════════════

def charger_cache() -> dict:
    if Path(CACHE_FILE).exists():
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    return {"derniere_verification": None, "dpe_vus": {}}


def sauvegarder_cache(cache: dict):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)

# ══════════════════════════════════════════════════════════════
#  COLLECTE
# ══════════════════════════════════════════════════════════════

def recuperer_dpe_bruts(date_depuis: str) -> list:
    """
    Récupère tous les DPE du 69003 depuis date_depuis.
    Pagination par curseur, sans filtre de zone.
    """
    url = (
        f"{API_BASE}"
        f"?size=100"
        f"&code_postal_ban_eq=69003"
        f"&date_reception_dpe_gte={date_depuis}"
    )
    headers = {"User-Agent": "Mozilla/5.0 (compatible; DPE-Monitor/1.0)"}
    tous = []

    while url:
        try:
            r = requests.get(url, timeout=30, headers=headers)
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            print(f"    ⚠️  Erreur API : {e}")
            break
        resultats = data.get("results", [])
        if not resultats:
            break
        tous.extend(resultats)
        url = data.get("next")

    return tous


def affecter_zone(dpe: dict) -> str:
    """Affecte un DPE à une zone selon ses coordonnées GPS."""
    geopoint = dpe.get("_geopoint", "")
    if not geopoint:
        return "Autre"
    try:
        lat, lng = map(float, geopoint.split(","))
    except Exception:
        return "Autre"

    for nom_zone, cfg in ZONES.items():
        if point_dans_polygone(lat, lng, cfg["polygone"]):
            return nom_zone
    return "Autre"

def formater_date(date_iso: str) -> str:
    try:
        return datetime.strptime(date_iso[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return date_iso

# ══════════════════════════════════════════════════════════════
#  EMAIL — AVEC DPE
# ══════════════════════════════════════════════════════════════

def generer_email_avec_dpe(resultats_par_zone: dict) -> tuple:
    total    = sum(len(v) for v in resultats_par_zone.values())
    date_str = datetime.now().strftime("%d/%m/%Y à %H:%M")

    pills = ""
    for nom, dpes in resultats_par_zone.items():
        pills += (
            f'<span style="display:inline-block;background:rgba(255,255,255,0.18);'
            f'border:1px solid rgba(255,255,255,0.3);padding:4px 16px;'
            f'border-radius:20px;margin:3px;font-size:13px;">'
            f'<strong>{len(dpes)}</strong> · {nom}</span>'
        )

    # Tri par date croissante pour chaque zone
    for nom_zone in resultats_par_zone:
        resultats_par_zone[nom_zone].sort(key=lambda d: d.get("date_reception_dpe", ""))

    lignes = ""
    for nom_zone, dpes in resultats_par_zone.items():
        lignes += f"""
        <tr>
          <td colspan="4" style="padding:8px 12px 4px;background:#f8fafc;
              border-top:2px solid #e5e7eb;border-bottom:1px solid #e5e7eb;">
            <span style="font-size:11px;font-weight:700;letter-spacing:0.5px;
                color:#6b7280;text-transform:uppercase;">📍 {nom_zone}</span>
            <span style="font-size:11px;color:#9ca3af;margin-left:8px;">
              — {len(dpes)} DPE</span>
          </td>
        </tr>"""

        for dpe in dpes:
            num_dpe    = dpe.get("numero_dpe", "—")
            date_r     = formater_date(dpe.get("date_reception_dpe", ""))
            adresse    = dpe.get("adresse_ban", "Adresse inconnue")
            cp         = dpe.get("code_postal_ban", "")
            commune    = dpe.get("nom_commune_ban", "")
            lien       = f"https://observatoire-dpe-audit.ademe.fr/afficher-dpe/{num_dpe}"
            etiquette  = (dpe.get("etiquette_dpe") or "?").upper()
            renouvellement = bool(dpe.get("numero_dpe_remplace"))
            type_batiment = (dpe.get("type_batiment") or "").lower()
            adresse_encoded = adresse.replace(" ", "+").replace(",", "")

            # Badge individuel / collectif
            if type_batiment == "immeuble":
                badge_type_bat = '<span style="display:inline-block;background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;padding:3px 8px;border-radius:12px;font-size:11px;font-weight:600;">🏢 Collectif</span>'
            elif type_batiment in ["appartement", "maison"]:
                label = "🏠 Maison" if type_batiment == "maison" else "🏠 Appart."
                badge_type_bat = f'<span style="display:inline-block;background:#f0fdf4;color:#15803d;border:1px solid #bbf7d0;padding:3px 8px;border-radius:12px;font-size:11px;font-weight:600;">{label}</span>'
            else:
                badge_type_bat = f'<span style="display:inline-block;background:#f8fafc;color:#64748b;border:1px solid #e2e8f0;padding:3px 8px;border-radius:12px;font-size:11px;">{type_batiment or "?"}</span>'

            # Couleurs officielles DPE
            couleurs_dpe = {
                "A": "#009F6B", "B": "#51B748", "C": "#CADD43",
                "D": "#F5E800", "E": "#F0A800", "F": "#E4581B", "G": "#D7221F"
            }
            couleur_bg  = couleurs_dpe.get(etiquette, "#9ca3af")
            texte_color = "#fff" if etiquette in ["A","B","C","F","G"] else "#111"

            # Mise en évidence des G
            row_style = ""
            if etiquette == "G":
                row_style = "background:#fff5f5;"

            # Badge DPE
            badge_dpe = (
                f'<span style="display:inline-block;background:{couleur_bg};color:{texte_color};'
                f'font-weight:900;font-size:18px;width:36px;height:36px;line-height:36px;'
                f'text-align:center;border-radius:6px;">{etiquette}</span>'
            )
            if etiquette == "G":
                badge_dpe += '<div style="font-size:10px;color:#d7221f;font-weight:700;margin-top:3px;">⚠️ Passoire</div>'

            # Badge renouvellement
            if renouvellement:
                badge_renouv = '<span style="display:inline-block;background:#f0fdf4;color:#166534;border:1px solid #bbf7d0;padding:3px 8px;border-radius:12px;font-size:11px;font-weight:600;">🔄 Renouvellement</span>'
            else:
                badge_renouv = '<span style="display:inline-block;background:#f8fafc;color:#64748b;border:1px solid #e2e8f0;padding:3px 8px;border-radius:12px;font-size:11px;">🆕 Premier</span>'

            lignes += f"""
        <tr style="{row_style}">
          <td style="padding:10px 12px;border-bottom:1px solid #f3f4f6;
              font-family:monospace;font-size:12px;color:#374151;white-space:nowrap;">
            {num_dpe}
          </td>
          <td style="padding:10px 12px;border-bottom:1px solid #f3f4f6;
              font-size:13px;color:#374151;white-space:nowrap;">
            {date_r}
          </td>
          <td style="padding:10px 12px;border-bottom:1px solid #f3f4f6;font-size:13px;">
            <a href="https://www.google.com/maps/search/?api=1&query={adresse_encoded}"
               style="font-weight:600;color:#111827;text-decoration:none;"
               title="Voir sur Google Maps">{adresse} 📍</a><br>
            <span style="color:#6b7280;font-size:12px;">{cp} {commune}</span>
          </td>
          <td style="padding:10px 12px;border-bottom:1px solid #f3f4f6;text-align:center;">
            {badge_dpe}
          </td>
          <td style="padding:10px 12px;border-bottom:1px solid #f3f4f6;text-align:center;">
            {badge_type_bat}
          </td>
          <td style="padding:10px 12px;border-bottom:1px solid #f3f4f6;text-align:center;">
            {badge_renouv}
          </td>
          <td style="padding:10px 12px;border-bottom:1px solid #f3f4f6;text-align:center;">
            <a href="{lien}"
               style="display:inline-block;background:#1d4ed8;color:#fff;
                      text-decoration:none;padding:6px 16px;border-radius:6px;
                      font-size:12px;font-weight:600;white-space:nowrap;">
              Voir →
            </a>
          </td>
        </tr>"""

    sujet = f"🏠 {total} nouveau{'x' if total > 1 else ''} DPE · Lyon 3e"

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:'Segoe UI',Helvetica,Arial,sans-serif;">
  <div style="max-width:100%;margin:16px auto;border-radius:12px;overflow:hidden;box-shadow:0 4px 32px rgba(0,0,0,0.10);">
    <div style="background:linear-gradient(135deg,#0f2942 0%,#1d4ed8 100%);padding:24px 20px;color:#fff;">
      <div style="font-size:11px;opacity:0.55;letter-spacing:1.5px;text-transform:uppercase;margin-bottom:8px;">DPE Prospector · Alerte quotidienne</div>
      <div style="font-size:28px;font-weight:700;margin-bottom:4px;">🏠 {total} nouveau{"x" if total > 1 else ""} DPE détecté{"s" if total > 1 else ""}</div>
      <div style="font-size:14px;opacity:0.75;margin-bottom:16px;">Diagnostics reçus dans ta zone de prospection</div>
      <div>{pills}</div>
      <div style="margin-top:14px;font-size:11px;opacity:0.4;">Généré le {date_str}</div>
    </div>
    <div style="background:#fff;">
      <table style="width:100%;border-collapse:collapse;">
        <thead>
          <tr style="background:#f8fafc;border-bottom:2px solid #e5e7eb;">
            <th style="padding:10px 12px;text-align:left;font-size:12px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;white-space:nowrap;">N° DPE</th>
            <th style="padding:10px 12px;text-align:left;font-size:12px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;white-space:nowrap;">Date</th>
            <th style="padding:10px 12px;text-align:left;font-size:12px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;">Adresse du bien</th>
            <th style="padding:10px 12px;text-align:center;font-size:12px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;">DPE</th>
            <th style="padding:10px 12px;text-align:center;font-size:12px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;">Logement</th>
            <th style="padding:10px 12px;text-align:center;font-size:12px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;">Type</th>
            <th style="padding:10px 12px;text-align:center;font-size:12px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;">Attestation</th>
          </tr>
        </thead>
        <tbody>{lignes}</tbody>
      </table>
    </div>
    <div style="background:#f8fafc;padding:12px 20px;border-top:1px solid #e5e7eb;text-align:center;">
      <p style="margin:0;font-size:11px;color:#9ca3af;">Données <a href="https://data.ademe.fr/datasets/dpe03existant" style="color:#6b7280;">ADEME Open Data</a> · Licence Etalab · Mise à jour en continu</p>
    </div>
  </div>
</body>
</html>"""

    return sujet, html


# ══════════════════════════════════════════════════════════════
#  EMAIL — AUCUN DPE
# ══════════════════════════════════════════════════════════════

def generer_email_vide() -> tuple:
    date_str = datetime.now().strftime("%d/%m/%Y à %H:%M")
    hier     = (datetime.now() - timedelta(days=1)).strftime("%d/%m/%Y")
    sujet    = "📋 Aucun nouveau DPE · Lyon 3e"

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:'Segoe UI',Helvetica,Arial,sans-serif;">
  <div style="max-width:600px;margin:32px auto;border-radius:12px;overflow:hidden;box-shadow:0 4px 32px rgba(0,0,0,0.10);">
    <div style="background:linear-gradient(135deg,#374151 0%,#6b7280 100%);padding:24px 20px;color:#fff;">
      <div style="font-size:11px;opacity:0.55;letter-spacing:1.5px;text-transform:uppercase;margin-bottom:8px;">DPE Prospector · Rapport quotidien</div>
      <div style="font-size:26px;font-weight:700;margin-bottom:6px;">📋 Aucun nouveau DPE</div>
      <div style="font-size:14px;opacity:0.75;">Zone surveillée : <strong>Lyon 3e arrondissement</strong></div>
      <div style="margin-top:14px;font-size:11px;opacity:0.4;">Généré le {date_str}</div>
    </div>
    <div style="background:#fff;padding:36px;">
      <p style="margin:0 0 12px;font-size:15px;color:#374151;line-height:1.6;">
        Aucun nouveau DPE n'a été déposé sur la zone du <strong>3e arrondissement de Lyon</strong>
        depuis le dernier rapport (veille du <strong>{hier}</strong>).
      </p>
      <p style="margin:0;font-size:14px;color:#6b7280;line-height:1.6;">Le prochain rapport sera envoyé demain matin à 8h.</p>
    </div>
    <div style="background:#f8fafc;padding:12px 20px;border-top:1px solid #e5e7eb;text-align:center;">
      <p style="margin:0;font-size:11px;color:#9ca3af;">Données <a href="https://data.ademe.fr/datasets/dpe03existant" style="color:#6b7280;">ADEME Open Data</a> · Licence Etalab · Mise à jour en continu</p>
    </div>
  </div>
</body>
</html>"""

    return sujet, html


# ══════════════════════════════════════════════════════════════
#  ENVOI EMAIL
# ══════════════════════════════════════════════════════════════

def envoyer_email(sujet: str, html: str):
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(EMAIL_EXPEDITEUR, EMAIL_MOT_DE_PASSE)
        for destinataire in [EMAIL_DESTINATAIRE, EMAIL_CC]:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = sujet
            msg["From"]    = EMAIL_EXPEDITEUR
            msg["To"]      = destinataire
            msg.attach(MIMEText(html, "html"))
            s.sendmail(EMAIL_EXPEDITEUR, destinataire, msg.as_string())
            print(f"   ✉️  Envoyé à {destinataire}")

# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print(f"🔍 DPE Prospector v5 · {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print("=" * 60)

    cache   = charger_cache()
    dpe_vus = cache.get("dpe_vus", {})

    # Fenêtre glissante de 30 jours — capture tous les DPE déposés tardivement
    # Le cache des N° DPE évite tout doublon dans la boîte mail
    date_depuis = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    if not cache.get("derniere_verification"):
        print(f"🆕 1ère exécution — remontée sur 30 jours")

    print(f"📅 Recherche depuis : {date_depuis}\n")

    # Récupération de tous les DPE du 69003
    print(f"📡 Récupération des DPE depuis l'API ADEME...")
    tous_dpe = recuperer_dpe_bruts(date_depuis)
    print(f"   → {len(tous_dpe)} DPE récupérés au total")

    # Affectation par zone et filtrage des doublons
    resultats_par_zone = {"Dauphiné-Lacassagne": [], "Montchat": [], "Part-Dieu": [], "Autre": []}

    for dpe in tous_dpe:
        num = dpe.get("numero_dpe")
        if not num or num in dpe_vus:
            continue
        zone = affecter_zone(dpe)
        resultats_par_zone[zone].append(dpe)

    # Tri par date croissante dans chaque zone
    for zone in resultats_par_zone:
        resultats_par_zone[zone].sort(key=lambda d: d.get("date_reception_dpe", ""))

    # Supprimer les zones vides
    resultats_par_zone = {k: v for k, v in resultats_par_zone.items() if v}

    for nom_zone, dpes in resultats_par_zone.items():
        print(f"📍 {nom_zone} : {len(dpes)} nouveau(x) DPE")

    for dpes in resultats_par_zone.values():
        for dpe in dpes:
            if dpe.get("numero_dpe"):
                dpe_vus[dpe["numero_dpe"]] = datetime.now().isoformat()

    cache["dpe_vus"]              = dpe_vus
    cache["derniere_verification"] = datetime.now().isoformat()
    sauvegarder_cache(cache)

    total = sum(len(v) for v in resultats_par_zone.values())
    print(f"\n{'=' * 60}")

    if resultats_par_zone:
        sujet, html = generer_email_avec_dpe(resultats_par_zone)
        print(f"📧 Envoi : {total} nouveau(x) DPE")
    else:
        sujet, html = generer_email_vide()
        print(f"📧 Envoi rapport vide")

    envoyer_email(sujet, html)
    print("✅ Email envoyé !")
    print("=" * 60)


if __name__ == "__main__":
    main()
