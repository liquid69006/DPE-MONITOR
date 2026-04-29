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

ZONES = json.loads(os.getenv("ZONES_JSON", json.dumps({
    "Lyon 3e": {
        "lat": 45.761,
        "lng": 4.849,
        "rayon_km": 1.4,
        "codes_postaux": ["69003"]
    },
})))

EMAIL_EXPEDITEUR   = os.getenv("EMAIL_EXPEDITEUR", "")
EMAIL_DESTINATAIRE = os.getenv("EMAIL_DESTINATAIRE", "dauphine.lacassagne@century21.fr")
EMAIL_CC           = os.getenv("EMAIL_CC",           "ybufferne@century21.fr")
EMAIL_MOT_DE_PASSE = os.getenv("EMAIL_MOT_DE_PASSE", "")

CACHE_FILE = "dpe_cache.json"
JOURS_HISTORIQUE_INITIAL = 30

# ══════════════════════════════════════════════════════════════
#  API ADEME
# ══════════════════════════════════════════════════════════════

API_BASE = "https://data.ademe.fr/data-fair/api/v1/datasets/dpe03existant/lines"

CHAMPS = [
    "N°DPE",
    "Date_réception_DPE",
    "Adresse_(BAN)",
    "Code_postal_(BAN)",
    "Nom__commune_(BAN)",
    "latitude",
    "longitude",
]

# ══════════════════════════════════════════════════════════════
#  GÉOGRAPHIE
# ══════════════════════════════════════════════════════════════

def distance_km(lat1, lng1, lat2, lng2) -> float:
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2))
         * math.sin(dlng / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))

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

def recuperer_dpe_zone(zone_cfg: dict, date_depuis: str) -> list:
    """
    Récupère les DPE via filtre exact sur le code postal.
    Filtre ensuite par rayon GPS et par date.
    """
    lat_c  = zone_cfg["lat"]
    lng_c  = zone_cfg["lng"]
    rayon  = zone_cfg["rayon_km"]
    codes  = zone_cfg.get("codes_postaux", ["69003"])

    tous = []

    for cp in codes:
        page = 0
        while True:
            params = {
                "size": 100,
                "page": page,
                "q": cp,
                "sort": "-Date_reception_DPE",
            }
            try:
                r = requests.get(API_BASE, params=params, timeout=30)
                r.raise_for_status()
                data = r.json()
            except requests.RequestException as e:
                print(f"    ⚠️  Erreur API (cp {cp}) : {e}")
                break

            resultats = data.get("results", [])
            if not resultats:
                break

            stop = False
            for dpe in resultats:
                date_str = dpe.get("Date_réception_DPE", "")
                if date_str and date_str < date_depuis:
                    stop = True
                    break

                lat_d = dpe.get("latitude")
                lng_d = dpe.get("longitude")
                if lat_d and lng_d:
                    if distance_km(lat_c, lng_c, float(lat_d), float(lng_d)) > rayon:
                        continue

                tous.append(dpe)

            if stop or len(resultats) < 100:
                break
            page += 1

    return tous

# ══════════════════════════════════════════════════════════════
#  FORMATAGE
# ══════════════════════════════════════════════════════════════

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

    lignes = ""
    for nom_zone, dpes in resultats_par_zone.items():
        lignes += f"""
        <tr>
          <td colspan="4" style="padding:10px 20px 6px;background:#f8fafc;
              border-top:2px solid #e5e7eb;border-bottom:1px solid #e5e7eb;">
            <span style="font-size:11px;font-weight:700;letter-spacing:0.5px;
                color:#6b7280;text-transform:uppercase;">📍 {nom_zone}</span>
            <span style="font-size:11px;color:#9ca3af;margin-left:8px;">
              — {len(dpes)} DPE</span>
          </td>
        </tr>"""

        for dpe in dpes:
            num_dpe = dpe.get("N°DPE", "—")
            date_r  = formater_date(dpe.get("Date_réception_DPE", ""))
            adresse = dpe.get("Adresse_(BAN)", "Adresse inconnue")
            cp      = dpe.get("Code_postal_(BAN)", "")
            commune = dpe.get("Nom__commune_(BAN)", "")
            lien    = f"https://observatoire-dpe-audit.ademe.fr/pub/dpe/{num_dpe}"

            lignes += f"""
        <tr>
          <td style="padding:14px 20px;border-bottom:1px solid #f3f4f6;
              font-family:monospace;font-size:13px;color:#374151;white-space:nowrap;">
            {num_dpe}
          </td>
          <td style="padding:14px 20px;border-bottom:1px solid #f3f4f6;
              font-size:13px;color:#374151;white-space:nowrap;">
            {date_r}
          </td>
          <td style="padding:14px 20px;border-bottom:1px solid #f3f4f6;font-size:13px;">
            <span style="font-weight:600;color:#111827;">{adresse}</span><br>
            <span style="color:#6b7280;font-size:12px;">{cp} {commune}</span>
          </td>
          <td style="padding:14px 20px;border-bottom:1px solid #f3f4f6;text-align:center;">
            <a href="{lien}"
               style="display:inline-block;background:#1d4ed8;color:#fff;
                      text-decoration:none;padding:6px 16px;border-radius:6px;
                      font-size:12px;font-weight:600;white-space:nowrap;">
              Voir l'attestation →
            </a>
          </td>
        </tr>"""

    sujet = f"🏠 {total} nouveau{'x' if total > 1 else ''} DPE · Lyon 3e"

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:'Segoe UI',Helvetica,Arial,sans-serif;">
  <div style="max-width:760px;margin:32px auto;border-radius:12px;overflow:hidden;box-shadow:0 4px 32px rgba(0,0,0,0.10);">
    <div style="background:linear-gradient(135deg,#0f2942 0%,#1d4ed8 100%);padding:32px 36px;color:#fff;">
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
            <th style="padding:12px 20px;text-align:left;font-size:12px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;white-space:nowrap;">N° DPE</th>
            <th style="padding:12px 20px;text-align:left;font-size:12px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;white-space:nowrap;">Date diagnostic</th>
            <th style="padding:12px 20px;text-align:left;font-size:12px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;">Adresse du bien</th>
            <th style="padding:12px 20px;text-align:center;font-size:12px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;">Attestation</th>
          </tr>
        </thead>
        <tbody>{lignes}</tbody>
      </table>
    </div>
    <div style="background:#f8fafc;padding:16px 36px;border-top:1px solid #e5e7eb;text-align:center;">
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
    <div style="background:linear-gradient(135deg,#374151 0%,#6b7280 100%);padding:32px 36px;color:#fff;">
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
    <div style="background:#f8fafc;padding:16px 36px;border-top:1px solid #e5e7eb;text-align:center;">
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

    if cache.get("derniere_verification"):
        date_depuis = cache["derniere_verification"][:10]
    else:
        date_depuis = (datetime.now() - timedelta(days=JOURS_HISTORIQUE_INITIAL)).strftime("%Y-%m-%d")
        print(f"🆕 1ère exécution — remontée sur {JOURS_HISTORIQUE_INITIAL} jours")

    print(f"📅 Recherche depuis : {date_depuis}\n")

    resultats_par_zone = {}

    for nom_zone, cfg in ZONES.items():
        print(f"📍 Zone : {nom_zone} (rayon {cfg['rayon_km']} km)")
        trouves  = recuperer_dpe_zone(cfg, date_depuis)
        nouveaux = [d for d in trouves if d.get("N°DPE") and d["N°DPE"] not in dpe_vus]
        print(f"   → {len(trouves)} récupérés · {len(nouveaux)} nouveaux")
        if nouveaux:
            resultats_par_zone[nom_zone] = nouveaux

    for dpes in resultats_par_zone.values():
        for dpe in dpes:
            if dpe.get("N°DPE"):
                dpe_vus[dpe["N°DPE"]] = datetime.now().isoformat()

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
