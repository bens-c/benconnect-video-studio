# BenConnect Video Studio 🎬

Ein webbasiertes Video Studio mit Replicate-Text-zu-Video-Anbindung, Clip-Bibliothek, einfacher Timeline und MP4-Export mit FFmpeg.

## Start

1. Kopiere `.env.example` nach `.env`.
2. Setze `ADMIN_PASSWORD` und einen langen zufälligen `SESSION_SECRET`.
3. Setze `REPLICATE_API_TOKEN` und `REPLICATE_MODEL` (z. B. `owner/model`). Prüfe die zum Modell passenden Parameter in `REPLICATE_INPUT_JSON`.
4. Starte `docker compose up -d --build`.
5. Lokal läuft die App hinter `127.0.0.1:8000`; für `video.benconnect.cyou` einen HTTPS-Reverse-Proxy und DNS einrichten.

Für lokale Tests ohne HTTPS kann `COOKIE_SECURE=false` verwendet werden; in Produktion muss es `true` bleiben.

## Funktionen

- Passwortgeschützter Zugang und serverseitiger API-Schlüssel
- Replicate-Videoerstellung mit Statusabfrage
- MP4-Uploads, Mediathek, Vorschau
- Clips sortieren, Startzeit und Länge festlegen
- Projekt-Timeline in SQLite speichern
- MP4-Export via FFmpeg

## Sicherheit und Grenzen

**Nicht ohne weitere Härtung als öffentliche Multiuser-Plattform betreiben.** Aktuell ist es eine Single-Admin-Anwendung. Es fehlen unter anderem Rate-Limiting, Quoten/Kostenlimits, getrennte Nutzerkonten, Job-Persistenz über Neustarts und vollständige SSRF-Abwehr beim Abruf von Modell-Outputs. Der Download sollte vor einem öffentlichen Deployment zusätzlich gegen DNS-Rebinding abgesichert werden. Exporte laufen synchron und können den Server belasten. Das Interface ist ein einfacher Editor, kein vollständiges Schnittprogramm.

**Nie `.env` oder echte API-Schlüssel committen.** Videogenerierung über Replicate verursacht Kosten.

## Deployment

Zieladresse: `https://video.benconnect.cyou`. Die DNS- und Reverse-Proxy-Konfiguration sowie der Replicate-Token müssen separat eingerichtet werden.
