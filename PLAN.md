# Historische Tages- und Stundendaten mit HAR-validiertem Backfill

## Zusammenfassung
Die HAR-Datei bestätigt, dass das Portal historische Tages-, Stunden- und Monatsdaten bereits über denselben Endpunkt liefert: `/bff/api/imsservice/v1/meters/{meter_id}/measurements` mit `filter=1DAY`, `1HOUR` und `1MONTH`. Damit ist die technische Richtung klar: Die Integration sollte die Messreihen strukturiert abrufen, fehlende Zeitpunkte rückwirkend nachziehen und die Daten als HA-Statistiken plus Status-/Hilfsentitäten bereitstellen.

Wichtige Befunde aus der HAR:
- Stundenwerte existieren real und werden vom Web-Frontend genutzt.
- Tageswerte und Stundenwerte kommen mit `startDatetime`/`endDatetime`, nicht mit `date`.
- `READING` und `CONSUMPTION` haben unterschiedliche Punktlogik:
  - `CONSUMPTION 1HOUR`: 24 Intervalle pro Tag
  - `READING 1HOUR`: 25 Punkte pro Tag, also inklusive Start-/Endstand
  - `CONSUMPTION 1DAY`: 30 Intervalle im 30-Tage-Fenster
  - `READING 1DAY`: 31 Punkte im 30-Tage-Fenster
- Das ist ein starkes Indiz: Fuer Historie/Statistiken sind Verbrauchs-/Einspeisewerte (`CONSUMPTION`/`FEEDIN`) die primaere Zeitreihe; Zaehlerstaende (`READING`) sind ergaenzend fuer Plausibilisierung und Diagnose.
- `1MONTH` existiert ebenfalls, ist aber fuer v1 nicht notwendig, solange Tages- und Stundenhistorie sauber laufen.

## Wichtige Aenderungen
- API-Client erweitern:
  - `measurements` auf generische Serienabfrage umbauen: `value_type`, `filter`, `start`, `end`.
  - Neue Modelle `MeasurementPoint` und `MeasurementSeries` mit `start_datetime`, `end_datetime`, `value`, `unit`, `status`.
  - Parser auf HAR-Struktur ausrichten: Felder `startDatetime`, `endDatetime`, `value`, `unit`, `status`, `minMeasurementStartDateTime`, `maxMeasurementEndDateTime`.
- Historienlogik fachlich trennen:
  - Primaere Historie aus Intervallwerten:
    - Verbrauchszaehler: `CONSUMPTION`
    - Einspeisezaehler: `FEEDIN`
  - Ergaenzende Zaehlerstandsreihe:
    - Verbrauchszaehler: `READING`
    - Einspeisezaehler: `FEEDIN_READING`
  - Fuer Backfill und Diagramme primaer Intervallwerte nutzen; `READING` nur fuer Diagnose, Plausibilisierung und optional eigene Statistik.
- Coordinator/Backfill:
  - Snapshot-Refresh beibehalten.
  - Zusaetzlich automatischen Backfill fuer die letzten 30 Tage ausfuehren.
  - Tages-Backfill:
    - komplettes 30-Tage-Fenster regelmaessig pruefen
    - fehlende Intervalle und nachtraeglich erschienene Werte ergaenzen/aktualisieren
  - Stunden-Backfill:
    - nicht pauschal 30 Tage mit 30 Einzelrequests pro Lauf
    - stattdessen Cursor-basiert und priorisiert:
      - letzte 2-3 Tage bei jedem Poll erneut pruefen
      - aeltere Tage nur bei erkannten Luecken oder in langsamer Rotation nachziehen
  - Lueckenmodell:
    - fehlender Intervallpunkt im erwarteten Fenster wird als Gap gespeichert
    - offene Gaps werden priorisiert erneut abgefragt
    - sehr junge Zeitraeume bekommen ein Delay-Fenster und gelten noch nicht als Gap
- Persistenz:
  - Lokale HA-Storage-Datei fuer Backfill-Metadaten je Zaehler:
    - letzter vollstaendig gepruefter Tagesbereich
    - letzter vollstaendig gepruefter Stundenbereich
    - bekannte offene Gaps
    - zuletzt beobachtete Datenobergrenze
  - Keine Vollkopie aller 30 Tage lokal speichern; die eigentliche Historie soll im Recorder/Statistiksystem leben.
- Darstellung:
  - Primaer HA-Statistiken/Langzeitdaten fuer Tages- und Stundenwerte.
  - Zusaetzliche Entitaeten fuer Sichtbarkeit und Diagnose:
    - letzter Tagespunkt
    - letzter Stundenpunkt
    - Anzahl offener Luecken
    - letzter Backfill-Lauf
    - Historienstatus
  - Keine grossen Rohdaten-Arrays in Entity-Attributen.

## Implementierungsdetails
- Wertsemantik aus HAR uebernehmen:
  - `CONSUMPTION`/`FEEDIN` sind Intervallenergien in `kWh`; diese eignen sich direkt fuer stuendliche/taegliche Verlaufshistorie.
  - `READING`-Reihen enthalten Randpunkte und nicht dieselbe Kardinalitaet wie Intervallreihen; sie duerfen nicht mit denselben Vollstaendigkeitsregeln geprueft werden.
- Delay-/Verfuegbarkeitslogik:
  - Tagesdaten sind offenbar nicht "live", sondern enden vor dem laufenden Tag; das ist normales Portalverhalten und kein Gap.
  - Stundenwerte scheinen fuer einzelne historische Tage vollstaendig abrufbar zu sein; der Plan sollte deshalb nicht den aktuellen Tag erzwingen, sondern nur abgeschlossene Intervalle bewerten.
  - Default:
    - Stunden-Gap-Pruefung erst fuer Intervalle aelter als 6 Stunden
    - Tages-Gap-Pruefung erst fuer Tage aelter als 1 voller Kalendertag
- Request-Strategie:
  - `1DAY` kann fuer 30 Tage in einem Request geladen werden.
  - `1HOUR` wird laut HAR tageweise abgefragt; die Integration sollte dieselbe Granularitaet uebernehmen.
  - `1MONTH` vorerst nur optional vorbereiten, nicht in v1-Backfill aufnehmen.
- Oeffentliche Interfaces/Typen:
  - Neue interne Modelle fuer Zeitreihen.
  - Runtime-Datenstruktur um einen `history_manager` oder gleichwertig erweitern.
  - Options vorbereiten:
    - `enable_daily_history = true`
    - `enable_hourly_history = true`
    - `history_backfill_days = 30`
    - optional spaeter `hourly_backfill_recheck_days = 3`

## Testplan
- HAR-basierte Parser-Tests:
  - Dekodierte Fixtures fuer `1DAY`, `1HOUR`, `1MONTH`.
  - Mapping von `startDatetime`/`endDatetime` auf interne Modelle.
  - Unterschiedliche Kardinalitaet zwischen `CONSUMPTION` und `READING` korrekt behandelt.
- Backfill-Tests:
  - Initialer Tages-Backfill ueber 30 Tage.
  - Stunden-Backfill fuer priorisierte letzte Tage.
  - Erkennung und spaetere Schliessung einzelner Luecken.
  - Erneuter Lauf mit identischen Daten ist idempotent.
  - Nachtraeglich geaenderter Portalwert ueberschreibt bestehenden Statistikpunkt.
- Verhaltens-/Regeltests:
  - aktueller noch unvollstaendiger Tag wird nicht als Fehler gezaehlt
  - aktueller noch junger Stundenbereich wird nicht als Gap gezaehlt
  - ein Meter mit Teilfehler blockiert andere Meter nicht
- HA-Integrationstests:
  - Statistik-IDs werden pro Zaehler und Aufloesung angelegt.
  - Status-/Hilfsentitaeten sind vorhanden.
  - Persistente Backfill-Metadaten werden nach Neustart weiterverwendet.

## Annahmen und Defaults
- Die HAR belegt Stundenwerte eindeutig; Stundenhistorie ist daher kein experimentelles Feature mehr, sondern kann als regulaere v1-Funktion geplant werden.
- Fuer Historie und Lueckenlogik werden Intervallwerte (`CONSUMPTION`/`FEEDIN`) als fuehrende Wahrheit behandelt.
- `READING` bleibt ergaenzend und wird nicht mit denselben "ein Punkt pro Intervall"-Regeln validiert.
- `1MONTH` wird vorerst nicht aktiv fuer Backfill genutzt, nur als moeglicher spaeterer Zusatz fuer Monatsuebersichten oder Plausibilisierung.
