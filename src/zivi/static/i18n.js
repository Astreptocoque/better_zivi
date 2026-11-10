/* Better Zivi — interface languages (French, German, Italian).

   French is the source language: t("Texte français", {n: 3}) returns the German or Italian
   version from the dictionaries below, or the French text itself. The static page
   (index.html) is translated by setLang(): every text and title/placeholder/aria-label is
   looked up the same way, and blocks marked data-i18n-html get their whole HTML from HTML.
   Server messages arrive as {key, params, text} and are translated with SERVER. */
"use strict";

const LOCALE = { fr: "fr-CH", de: "de-CH", it: "it-CH" };
const LANG_NATIVE = { fr: "Français", de: "Deutsch", it: "Italiano" };
const FLAGS = {
  fr: '<svg class="flag" viewBox="0 0 3 2" aria-hidden="true"><rect width="1" height="2" fill="#0055A4"/><rect x="1" width="1" height="2" fill="#fff"/><rect x="2" width="1" height="2" fill="#EF4135"/></svg>',
  de: '<svg class="flag" viewBox="0 0 5 3" aria-hidden="true"><rect width="5" height="1" fill="#000"/><rect y="1" width="5" height="1" fill="#DD0000"/><rect y="2" width="5" height="1" fill="#FFCE00"/></svg>',
  it: '<svg class="flag" viewBox="0 0 3 2" aria-hidden="true"><rect width="1" height="2" fill="#009246"/><rect x="1" width="1" height="2" fill="#fff"/><rect x="2" width="1" height="2" fill="#CE2B37"/></svg>',
};

/* first visit: the browser's language if it is one of ours */
let LANG = (() => {
  try {
    const saved = JSON.parse(localStorage.getItem("zivi.lang") || "null");
    if (LOCALE[saved]) return saved;
  } catch { /* private mode */ }
  const nav = (navigator.language || "fr").slice(0, 2);
  return LOCALE[nav] ? nav : "fr";
})();

const I18N = {
  de: {
    // ---- general
    "Oui": "Ja", "Non": "Nein", "Possible": "Möglich", "Autre": "Andere",
    "Carte nationale (gris)": "Landeskarte (grau)", "Carte nationale (couleur)": "Landeskarte (farbig)",
    "Détails": "Details", "Point": "Punkt",
    "classées par l’IA": "nach KI sortiert", "mots-clés + IA": "Stichwörter + KI",
    "favoris uniquement": "nur Favoriten", "affectation": "Einsatz", "affectations": "Einsätze",
    "Aucune affectation chargée pour l’instant.": "Noch keine Einsätze geladen.",
    "Charger un export JSON": "JSON-Export laden",
    "Vous n’avez encore aucun favori. Cliquez sur l’étoile ☆ d’une affectation pour l’ajouter.": "Sie haben noch keine Favoriten. Klicken Sie auf den Stern ☆ eines Einsatzes, um ihn hinzuzufügen.",
    "Afficher toutes les affectations": "Alle Einsätze anzeigen",
    "Aucune affectation ne correspond à tous ces filtres.": "Kein Einsatz erfüllt alle diese Filter.",
    "Effacer tous les filtres": "Alle Filter löschen",
    "min. {n} sem.": "mind. {n} Wo.", "{n} h/sem.": "{n} Std./Wo.",
    "Logement": "Unterkunft", "Repas": "Verpflegung", "Week-ends": "Wochenenden", "Nuits": "Nächte",
    "dès le {date}": "ab {date}", "Original : {title}": "Original: {title}",
    "{count} sur l’ensemble ({pct}) ont cette caractéristique": "{count} insgesamt ({pct}) haben dieses Merkmal",
    "cliquer pour retirer ce filtre": "klicken, um diesen Filter zu entfernen",
    "cliquer pour les afficher": "klicken, um sie anzuzeigen",
    "Retirer {what}": "{what} entfernen", "Retirer ce filtre": "Diesen Filter entfernen", "Tout retirer": "Alle entfernen",
    "Langue": "Sprache", "Domaine d’activité": "Tätigkeitsbereich", "Modèle d’horaire": "Arbeitszeitmodell",
    "Tous": "Alle", "Rayon en km pour {place}": "Radius in km für {place}", "Aucun lieu trouvé": "Kein Ort gefunden",
    "Retirer des favoris": "Aus Favoriten entfernen", "Ajouter aux favoris": "Zu Favoriten hinzufügen",
    // ---- detail
    "N°": "Nr.", "Ouvrir sur ZiviConnect": "Auf ZiviConnect öffnen", "Voir sur la carte": "Auf der Karte zeigen",
    "Traduire le texte de l’affectation dans la langue de l’interface avec le traducteur intégré du navigateur": "Den Text des Einsatzes mit dem integrierten Übersetzer des Browsers in die Sprache der Oberfläche übersetzen",
    "Traduire": "Übersetzen", "Durée minimale": "Mindestdauer", "Heures par semaine": "Stunden pro Woche",
    "Travail le week-end": "Wochenendarbeit", "Travail de nuit": "Nachtarbeit",
    "Contrôle de réputation": "Leumundsprüfung", "Programme prioritaire": "Schwerpunktprogramm",
    "À l’étranger": "Im Ausland", "Ce qui la rend rare": "Was ihn selten macht", "Tâches": "Aufgaben",
    "Compétences requises": "Vorausgesetzte Kenntnisse", "Compétences souhaitées": "Erwünschte Kenntnisse",
    "Cours du service civil": "Zivildienst-Kurse", "Semaines de début disponibles": "Verfügbare Startwochen",
    "À propos de l’établissement": "Über den Einsatzbetrieb", "Adresse de l’affectation": "Einsatzadresse",
    "Contact": "Kontakt", "Comment postuler": "Bewerbung",
    "Traduction enregistrée dans la base.": "In der Datenbank gespeicherte Übersetzung.",
    "Voir l’original": "Original anzeigen", "Aucune date publiée.": "Keine Daten veröffentlicht.",
    // ---- browser translator
    "Préparation du traducteur du navigateur…": "Übersetzer des Browsers wird vorbereitet…",
    "le traducteur intégré du navigateur ne répond pas": "der integrierte Übersetzer des Browsers antwortet nicht",
    "Téléchargement du modèle de traduction…": "Übersetzungsmodell wird heruntergeladen…",
    "Téléchargement du modèle de traduction : {pct} %": "Übersetzungsmodell wird heruntergeladen: {pct} %",
    "le navigateur ne sait pas traduire depuis {lang}": "der Browser kann {lang} nicht übersetzen",
    "Ce navigateur n’a pas de traducteur intégré utilisable par la page. Utilisez un Chrome ou Edge récent, ou faites un clic droit sur le texte → « Traduire ».": "Dieser Browser hat keinen integrierten Übersetzer, den die Seite nutzen kann. Verwenden Sie ein aktuelles Chrome oder Edge, oder klicken Sie mit der rechten Maustaste auf den Text → «Übersetzen».",
    "Ce texte est déjà dans la langue de l’interface.": "Dieser Text ist bereits in der Sprache der Oberfläche.",
    "Le navigateur ne sait pas traduire depuis {lang}.": "Der Browser kann {lang} nicht übersetzen.",
    "Traduction en cours…": "Übersetzung läuft…",
    "Traduit depuis {lang} par le traducteur intégré du navigateur.": "Vom integrierten Übersetzer des Browsers übersetzt (Originalsprache: {lang}).",
    "La traduction a échoué : {error}. Faites un clic droit sur le texte → « Traduire », ou utilisez un Chrome ou Edge récent.": "Die Übersetzung ist fehlgeschlagen: {error}. Klicken Sie mit der rechten Maustaste auf den Text → «Übersetzen», oder verwenden Sie ein aktuelles Chrome oder Edge.",
    // ---- import
    "Envoi de {file} ({size} Mo)…": "{file} wird gesendet ({size} MB)…",
    "Échec de l’envoi : {error}": "Senden fehlgeschlagen: {error}", "Échec de l’import : {error}": "Import fehlgeschlagen: {error}",
    "{n} affectations chargées — {located} placées sur la carte, {weeks} avec des dates de début.": "{n} Einsätze geladen — {located} auf der Karte, {weeks} mit Startdaten.",
    "{n} Mo": "{n} MB", "{n} Ko": "{n} KB", "{n} Go": "{n} GB",
    // ---- database window
    "sur la carte": "auf der Karte", "favoris": "Favoriten", "textes traduits": "übersetzte Texte",
    "index IA": "KI-Index", "pas construit": "nicht erstellt", "Fichier importé": "Importierte Datei",
    "Importé le": "Importiert am", "Semaines de début": "Startwochen", "du {from} au {to}": "vom {from} bis {to}",
    "Fichier de la base": "Datenbankdatei", "Copier": "Kopieren", "Taille": "Grösse", "Copié": "Kopiert",
    "Copie impossible": "Kopieren nicht möglich", "{n} vecteurs ({model})": "{n} Vektoren ({model})", "aucun": "keiner",
    "Effacer tous les favoris ? C’est définitif.": "Alle Favoriten löschen? Das ist endgültig.",
    "Supprimer les traductions en {lang} ? Les autres langues sont conservées.": "Die Übersetzungen auf {lang} löschen? Die anderen Sprachen bleiben erhalten.",
    "Supprimer l’index IA de ce modèle (dans cette langue) ? Il faudra le reconstruire.": "Den KI-Index dieses Modells (in dieser Sprache) löschen? Er muss dann neu erstellt werden.",
    // ---- translation
    "Rien à traduire": "Nichts zu übersetzen",
    "{done} / {texts} textes · {adone} / {assignments} affectations entièrement traduites": "{done} / {texts} Texte · {adone} / {assignments} Einsätze vollständig übersetzt",
    "Toutes les affectations sont déjà en {lang}.": "Alle Einsätze sind bereits auf {lang}.",
    "Reprendre la traduction": "Übersetzung fortsetzen", "Traduire la base en {lang}": "Datenbank auf {lang} übersetzen",
    "Ce navigateur n’a pas de traducteur intégré utilisable par la page : utilisez un Chrome ou Edge récent, ou Ollama.": "Dieser Browser hat keinen integrierten Übersetzer, den die Seite nutzen kann: Verwenden Sie ein aktuelles Chrome oder Edge, oder Ollama.",
    "{n} h": "{n} Std.", "{n} min": "{n} Min.",
    "Ce navigateur n’a pas de traducteur intégré : utilisez un Chrome ou Edge récent.": "Dieser Browser hat keinen integrierten Übersetzer: Verwenden Sie ein aktuelles Chrome oder Edge.",
    "Le navigateur ne sait pas traduire depuis {lang} : ces textes seront gardés tels quels.": "Der Browser kann {lang} nicht übersetzen: Diese Texte bleiben unverändert.",
    "le traducteur échoue sans arrêt ({error})": "der Übersetzer scheitert ständig ({error})",
    "Traduction en cours… {rate} caractères/s · encore {eta} environ": "Übersetzung läuft… {rate} Zeichen/s · noch etwa {eta}",
    "dernière erreur : {error}": "letzter Fehler: {error}",
    "« Reprendre la traduction » les réessaie.": "«Übersetzung fortsetzen» versucht sie erneut.",
    "En pause. « Reprendre la traduction » continue là où elle s’est arrêtée.": "Pausiert. «Übersetzung fortsetzen» macht dort weiter, wo sie aufgehört hat.",
    "Traduction terminée.": "Übersetzung abgeschlossen.",
    "La traduction s’est arrêtée : {error}. Ce qui est fait est enregistré ; vous pouvez reprendre.": "Die Übersetzung wurde unterbrochen: {error}. Das Erledigte ist gespeichert; Sie können fortsetzen.",
    "Mise en pause après le texte en cours…": "Pause nach dem aktuellen Text…",
    "La traduction tourne sur cet ordinateur, dans Ollama : vous pouvez fermer cette fenêtre, et même la page. Ce qui est traduit est enregistré au fur et à mesure ; « Traduire la base » reprend là où elle s’est arrêtée.": "Die Übersetzung läuft auf diesem Computer, in Ollama: Sie können dieses Fenster und sogar die Seite schliessen. Das Übersetzte wird laufend gespeichert; «Datenbank übersetzen» macht dort weiter, wo sie aufgehört hat.",
    "La traduction se fait dans cette page : vous pouvez fermer cette fenêtre et continuer à chercher, mais gardez la page ouverte. Ce qui est traduit est enregistré au fur et à mesure ; « Traduire la base » reprend là où elle s’est arrêtée.": "Die Übersetzung läuft in dieser Seite: Sie können dieses Fenster schliessen und weitersuchen, aber lassen Sie die Seite offen. Das Übersetzte wird laufend gespeichert; «Datenbank übersetzen» macht dort weiter, wo sie aufgehört hat.",
    "Ollama ne répond pas : démarrez-le ou installez-le ci-dessus.": "Ollama antwortet nicht: Starten oder installieren Sie es oben.",
    "Téléchargez d’abord le modèle {model} (bouton ci-dessus).": "Laden Sie zuerst das Modell {model} herunter (Schaltfläche oben).",
    "Traduction en {lang} avec {model}…": "Übersetzung auf {lang} mit {model}…",
    "{rate} caractères/s · encore {eta} environ": "{rate} Zeichen/s · noch etwa {eta}",
    "premier texte (le modèle se charge en mémoire)": "erster Text (das Modell wird in den Speicher geladen)",
    "En pause{aside}. « Reprendre la traduction » continue là où elle s’est arrêtée.": "Pausiert{aside}. «Übersetzung fortsetzen» macht dort weiter, wo sie aufgehört hat.",
    "Traduction terminée{aside}.": "Übersetzung abgeschlossen{aside}.",
    "« Reprendre la traduction » réessaie les textes mis de côté.": "«Übersetzung fortsetzen» versucht die zurückgestellten Texte erneut.",
    "Suivi interrompu : {error}": "Verfolgung unterbrochen: {error}",
    // ---- Ollama box
    "Impossible de joindre le serveur de l’application.": "Der Server der Anwendung ist nicht erreichbar.",
    "Ollama n’est pas installé sur cet ordinateur. Installez-le (gratuit), puis revenez ici.": "Ollama ist auf diesem Computer nicht installiert. Installieren Sie es (kostenlos) und kommen Sie dann hierher zurück.",
    "Télécharger Ollama ↗": "Ollama herunterladen ↗", "Vérifier à nouveau": "Erneut prüfen",
    "Ollama est installé mais ne tourne pas.": "Ollama ist installiert, läuft aber nicht.", "Démarrer Ollama": "Ollama starten",
    "Téléchargement de {model} : {progress}…": "{model} wird heruntergeladen: {progress}…",
    "Ollama {version} · modèle {model} installé{size}.": "Ollama {version} · Modell {model} installiert{size}.",
    "Le téléchargement a échoué : {error}": "Der Download ist fehlgeschlagen: {error}",
    "Ollama {version} est prêt, mais le modèle {model} n’est pas installé.": "Ollama {version} ist bereit, aber das Modell {model} ist nicht installiert.",
    "Télécharger le modèle": "Modell herunterladen",
    "Légende des couleurs": "Farblegende", "Couleurs : domaine d’activité": "Farben: Tätigkeitsbereich",
    "Bulle : plusieurs affectations proches, en parts par domaine ; le nombre au centre.": "Blase: mehrere nahe Einsätze, in Anteilen nach Bereich; die Zahl in der Mitte.",
    "Anneau doré : favori.": "Goldener Ring: Favorit.",
    "Filtrer : afficher les affectations avec cette valeur": "Filtern: Einsätze mit diesem Wert anzeigen",
    "Carte en plein écran": "Karte im Vollbild", "Quitter la carte en plein écran": "Vollbild der Karte verlassen",
    "Libérer la mémoire et arrêter Ollama quand la page est fermée": "Speicher freigeben und Ollama beenden, wenn die Seite geschlossen wird",
    "Environ une minute et demie après la fermeture de la dernière page, les modèles sont retirés de la mémoire et Ollama est arrêté, sauf pendant une traduction ou la construction d’un index. Il redémarre à la prochaine visite.": "Etwa anderthalb Minuten nach dem Schliessen der letzten Seite werden die Modelle aus dem Speicher entfernt und Ollama beendet, ausser während einer Übersetzung oder der Erstellung eines Index. Beim nächsten Besuch startet es wieder.",
    // ---- AI search
    "enregistré ({hint}) — laisser vide pour le conserver": "gespeichert ({hint}) — leer lassen, um ihn zu behalten",
    "texte original": "Originaltext", "trop long ou trop lent": "zu lang oder zu langsam",
    "Construction de l’index : {done} / {total} · lots de {batch}": "Index wird erstellt: {done} / {total} · Pakete zu {batch}",
    "La dernière construction s’est arrêtée : {error}. Ce qui est fait est gardé ; « Construire l’index » reprend.": "Die letzte Erstellung wurde unterbrochen: {error}. Das Erledigte bleibt erhalten; «Index erstellen» setzt fort.",
    "Arrêtée à {done} / {total}. « Construire l’index » reprend là où elle s’est arrêtée.": "Angehalten bei {done} / {total}. «Index erstellen» macht dort weiter, wo sie aufgehört hat.",
    "Index IA prêt : {n} affectations ({model}){skipped}. Choisissez « IA » ou « Les deux » à côté du champ de recherche.": "KI-Index bereit: {n} Einsätze ({model}){skipped}. Wählen Sie «KI» oder «Beides» neben dem Suchfeld.",
    "Pas encore d’index IA pour ce modèle dans cette langue.": "Noch kein KI-Index für dieses Modell in dieser Sprache.",
    "{count} : « Construire l’index » s’en charge.": "{count}: «Index erstellen» erledigt das.",
    "Utilise la traduction dans la langue de l’interface quand elle existe (Traduction), sinon le texte original : {have} affectations sur {total} sont en {lang} ({native} d’origine, {translated} traduites). Chaque langue garde son propre index.": "Verwendet die Übersetzung in der Sprache der Oberfläche, wenn es sie gibt (Übersetzung), sonst den Originaltext: {have} von {total} Einsätzen sind auf {lang} ({native} im Original, {translated} übersetzt). Jede Sprache behält ihren eigenen Index.",
    "Index déjà construits : {list}.": "Bereits erstellte Indexe: {list}.",
    "Connecté. Le modèle renvoie des vecteurs de {n} dimensions.": "Verbunden. Das Modell liefert Vektoren mit {n} Dimensionen.",
    "Tout reconstruire ? Les vecteurs de ce modèle (dans cette langue) seront recalculés pour toutes les affectations.": "Alles neu erstellen? Die Vektoren dieses Modells (in dieser Sprache) werden für alle Einsätze neu berechnet.",
    "Arrêt après le lot en cours…": "Stopp nach dem aktuellen Paket…",
    // ---- plurals
    "{n} semaine": "{n} Woche", "{n} semaines": "{n} Wochen",
    "{n} lieu sur la carte": "{n} Ort auf der Karte", "{n} lieux sur la carte": "{n} Orte auf der Karte",
    "{n} résultat sans localisation": "{n} Ergebnis ohne Standort", "{n} résultats sans localisation": "{n} Ergebnisse ohne Standort",
    "{n} affectation": "{n} Einsatz", "{n} affectations": "{n} Einsätze",
    "{n} favori": "{n} Favorit", "{n} favoris": "{n} Favoriten",
    "{n} texte traduit": "{n} übersetzter Text", "{n} textes traduits": "{n} übersetzte Texte",
    "{n} texte mis de côté": "{n} zurückgestellter Text", "{n} textes mis de côté": "{n} zurückgestellte Texte",
    "{n} texte n’a pas pu être traduit": "{n} Text konnte nicht übersetzt werden", "{n} textes n’ont pas pu être traduits": "{n} Texte konnten nicht übersetzt werden",
    "{n} texte ignoré": "{n} übersprungener Text", "{n} textes ignorés": "{n} übersprungene Texte",
    "{n} affectation est à calculer": "{n} Einsatz ist zu berechnen", "{n} affectations sont à calculer": "{n} Einsätze sind zu berechnen",
    // ---- values and names
    "Domaine": "Bereich", "Horaire": "Arbeitszeit", "Canton": "Kanton", "Tâche": "Aufgabe",
    "Allemand": "Deutsch", "Français": "Französisch", "Italien": "Italienisch",
    "Base de données": "Datenbank", "Traduction": "Übersetzung", "Recherche IA": "KI-Suche",
    "allemand": "Deutsch", "français": "Französisch", "italien": "Italienisch", "anglais": "Englisch",
    "moins de 40 h": "unter 40 Std.", "40 à 42,5 h": "40 bis 42,5 Std.", "43 à 45 h": "43 bis 45 Std.", "plus de 45 h": "über 45 Std.",
    // ---- static page
    "Accueil Better Zivi": "Startseite Better Zivi",
    "Rechercher des tâches, établissements, lieux…": "Aufgaben, Einsatzbetriebe, Orte suchen…",
    "Recherche avancée": "Erweiterte Suche", "Effacer": "Leeren", "Tous ces mots": "Alle diese Wörter",
    "jardin cuisine": "Garten Küche", "Cette expression exacte": "Genau dieser Ausdruck", "aide en cuisine": "Mithilfe in der Küche",
    "Au moins un de ces mots": "Mindestens eines dieser Wörter", "chien chat cheval": "Hund Katze Pferd",
    "Aucun de ces mots": "Keines dieser Wörter", "nuit week-end": "Nacht Wochenende", "Requête :": "Suchanfrage:",
    "Fermer": "Schliessen", "Mode de recherche": "Suchmodus", "Mots-clés": "Stichwörter",
    "Recherche par le sens, nécessite un jeton IA": "Suche nach Bedeutung, braucht ein KI-Token", "IA": "KI",
    "Mots-clés et IA combinés": "Stichwörter und KI kombiniert", "Les deux": "Beides",
    "Afficher uniquement les favoris": "Nur Favoriten anzeigen", "Favoris": "Favoriten",
    "Base de données : import, traduction, recherche IA": "Datenbank: Import, Übersetzung, KI-Suche",
    "Télécharger les résultats actuels en CSV": "Aktuelle Ergebnisse als CSV herunterladen", "Exporter CSV": "CSV exportieren",
    "Panneaux": "Bereiche", "Filtres": "Filter", "Résultats": "Ergebnisse", "Carte": "Karte", "Tout effacer": "Alles löschen",
    "Période": "Zeitraum", "Du": "Von", "Au": "Bis",
    "Début dans cette période, pour au moins": "Beginn in diesem Zeitraum, für mindestens",
    "semaines consécutives": "Wochen am Stück", "Disponible chaque semaine de la période": "Jede Woche des Zeitraums verfügbar",
    "Autour de lieux": "Um Orte herum", "Localité ou NPA, p. ex. Lausanne ou 3800": "Ort oder PLZ, z. B. Bern oder 3800",
    "Rayon": "Radius", "Ajouter un lieu en cliquant sur la carte": "Ort durch Klick auf die Karte hinzufügen",
    "Seulement ce qui est visible sur la carte": "Nur was auf der Karte sichtbar ist", "Conditions": "Bedingungen",
    "Logement offert": "Unterkunft angeboten", "Repas offerts": "Verpflegung angeboten",
    "Aucun cours du service civil requis": "Kein Zivildienst-Kurs erforderlich", "Durée et horaires": "Dauer und Arbeitszeit",
    "Durée min. de": "Mindestdauer von", "semaines": "Wochen", "à": "bis", "Heures/semaine de": "Std./Woche von",
    "Types de tâches": "Aufgabenbereiche", "au moins un": "mindestens einer", "tous": "alle", "Plus": "Mehr",
    "La langue est déduite du texte de la description.": "Die Sprache wird aus dem Beschreibungstext abgeleitet.",
    "Chargement…": "Wird geladen…",
    "Trier par rareté : d’abord les affectations aux caractéristiques que peu d’autres partagent": "Nach Seltenheit sortieren: zuerst Einsätze mit Merkmalen, die nur wenige andere haben",
    "✨ Raretés": "✨ Raritäten", "Trier": "Sortieren", "Pertinence": "Relevanz", "Distance": "Entfernung",
    "Début le plus tôt": "Frühester Beginn", "Durée minimale la plus courte": "Kürzeste Mindestdauer",
    "Plus récentes": "Neueste", "Titre": "Titel", "Rareté": "Seltenheit", "Raretés sélectionnées": "Gewählte Raritäten",
    "Afficher plus": "Mehr anzeigen", "Chargement des données": "Daten werden geladen", "Envoi…": "Wird gesendet…",
    "Sections": "Abschnitte", "Réglages": "Einstellungen", "Données": "Daten",
    "Charger un export JSON…": "JSON-Export laden…",
    "Remplace les affectations. Les favoris, les traductions, l’index IA et les lieux déjà trouvés sont conservés.": "Ersetzt die Einsätze. Favoriten, Übersetzungen, KI-Index und bereits gefundene Orte bleiben erhalten.",
    "Options avancées": "Erweiterte Optionen", "Ces actions sont définitives.": "Diese Aktionen sind endgültig.",
    "Effacer les favoris": "Favoriten löschen", "Traductions": "Übersetzungen", "Supprimer les traductions": "Übersetzungen löschen",
    "Index IA": "KI-Index", "Supprimer l’index IA": "KI-Index löschen",
    "Traduit dans la langue de l’interface les affectations rédigées dans une autre langue. Le modèle tourne sur cet ordinateur : gratuit, et rien n’est envoyé sur Internet. Chaque langue garde ses propres traductions.": "Übersetzt die Einsätze, die in einer anderen Sprache verfasst sind, in die Sprache der Oberfläche. Das Modell läuft auf diesem Computer: kostenlos, und nichts wird ins Internet gesendet. Jede Sprache behält ihre eigenen Übersetzungen.",
    "Moteur de traduction": "Übersetzungsmodul", "recommandé": "empfohlen",
    "Modèle de traduction local, rapide ; continue même si la page est fermée.": "Lokales Übersetzungsmodell, schnell; läuft weiter, auch wenn die Seite geschlossen ist.",
    "Navigateur": "Browser", "Traducteur intégré de Chrome ou Edge, sans installation, mais lent.": "Integrierter Übersetzer von Chrome oder Edge, ohne Installation, aber langsam.",
    "Modèle": "Modell", "Traduire la base": "Datenbank übersetzen", "Pause": "Pause", "Affichage et recherche": "Anzeige und Suche",
    "Afficher les affectations dans la langue de l’interface": "Einsätze in der Sprache der Oberfläche anzeigen",
    "Titres, tâches et descriptions traduits dans la liste, la carte et la fiche. La fiche garde un bouton « Voir l’original ».": "Übersetzte Titel, Aufgaben und Beschreibungen in der Liste, auf der Karte und im Detail. Das Detail behält eine Schaltfläche «Original anzeigen».",
    "Chercher aussi dans le texte original": "Auch im Originaltext suchen",
    "Désactivé : seuls les textes dans la langue de l’interface (originaux ou traduits) sont cherchés, et une affectation pas encore traduite ne ressort pas.": "Ausgeschaltet: Nur Texte in der Sprache der Oberfläche (original oder übersetzt) werden durchsucht, und ein noch nicht übersetzter Einsatz erscheint nicht.",
    "Trouve des affectations par le sens (« travailler avec des animaux en plein air ») grâce aux embeddings de texte. Fonctionne avec tout service compatible OpenAI, ou un Ollama local sans jeton. Le jeton reste dans la mémoire du serveur local et n’est jamais écrit sur le disque.": "Findet Einsätze nach Bedeutung («mit Tieren im Freien arbeiten») dank Text-Embeddings. Funktioniert mit jedem OpenAI-kompatiblen Dienst oder einem lokalen Ollama ohne Token. Das Token bleibt im Speicher des lokalen Servers und wird nie auf die Festplatte geschrieben.",
    "Index": "Index", "Construire l’index dans la langue de l’interface": "Index in der Sprache der Oberfläche erstellen",
    "Construire l’index": "Index erstellen", "Arrêter": "Anhalten",
    "Efface les vecteurs de ce modèle et recalcule toutes les affectations": "Löscht die Vektoren dieses Modells und berechnet alle Einsätze neu",
    "Tout reconstruire": "Alles neu erstellen",
    "« Construire » ne calcule que les affectations nouvelles ou modifiées et reprend après un arrêt. Avec un modèle local, les lots trop lents sont réessayés puis découpés.": "«Erstellen» berechnet nur neue oder geänderte Einsätze und setzt nach einem Stopp fort. Mit einem lokalen Modell werden zu langsame Pakete erneut versucht und dann aufgeteilt.",
    "Service": "Dienst", "Fournisseur": "Anbieter",
    "Ollama sur cet ordinateur — nomic-embed-text": "Ollama auf diesem Computer — nomic-embed-text", "Personnalisé": "Benutzerdefiniert",
    "URL de base de l’API": "Basis-URL der API", "Jeton": "Token", "Mémoriser le jeton dans ce navigateur": "Token in diesem Browser speichern",
    "Enregistrer et tester la connexion": "Speichern und Verbindung testen", "Résultats retenus par la recherche IA": "Von der KI-Suche behaltene Ergebnisse",
    "Les 50 plus proches": "Die 50 nächsten", "Les 100 plus proches": "Die 100 nächsten", "Les 200 plus proches": "Die 200 nächsten",
    "Les 500 plus proches": "Die 500 nächsten", "Toutes, classées par proximité": "Alle, nach Nähe sortiert",
    "3,3 Go — rapide, recommandé": "3,3 GB — schnell, empfohlen",
    "8,1 Go — meilleure qualité, demande ~16 Go de mémoire": "8,1 GB — bessere Qualität, braucht ~16 GB Speicher",
    "17 Go — la meilleure, très lourd": "17 GB — die beste, sehr schwer",
  },
  it: {
    // ---- general
    "Oui": "Sì", "Non": "No", "Possible": "Possibile", "Autre": "Altro",
    "Carte nationale (gris)": "Carta nazionale (grigio)", "Carte nationale (couleur)": "Carta nazionale (a colori)",
    "Détails": "Dettagli", "Point": "Punto",
    "classées par l’IA": "ordinati dall’IA", "mots-clés + IA": "parole chiave + IA",
    "favoris uniquement": "solo preferiti", "affectation": "impiego", "affectations": "impieghi",
    "Aucune affectation chargée pour l’instant.": "Nessun impiego caricato per ora.",
    "Charger un export JSON": "Carica un export JSON",
    "Vous n’avez encore aucun favori. Cliquez sur l’étoile ☆ d’une affectation pour l’ajouter.": "Non hai ancora preferiti. Clicca sulla stella ☆ di un impiego per aggiungerlo.",
    "Afficher toutes les affectations": "Mostra tutti gli impieghi",
    "Aucune affectation ne correspond à tous ces filtres.": "Nessun impiego corrisponde a tutti questi filtri.",
    "Effacer tous les filtres": "Cancella tutti i filtri",
    "min. {n} sem.": "min. {n} sett.", "{n} h/sem.": "{n} h/sett.",
    "Logement": "Alloggio", "Repas": "Vitto", "Week-ends": "Fine settimana", "Nuits": "Notti",
    "dès le {date}": "dal {date}", "Original : {title}": "Originale: {title}",
    "{count} sur l’ensemble ({pct}) ont cette caractéristique": "{count} in totale ({pct}) hanno questa caratteristica",
    "cliquer pour retirer ce filtre": "clicca per togliere questo filtro",
    "cliquer pour les afficher": "clicca per mostrarli",
    "Retirer {what}": "Togli {what}", "Retirer ce filtre": "Togli questo filtro", "Tout retirer": "Togli tutto",
    "Langue": "Lingua", "Domaine d’activité": "Settore d’attività", "Modèle d’horaire": "Modello d’orario",
    "Tous": "Tutti", "Rayon en km pour {place}": "Raggio in km per {place}", "Aucun lieu trouvé": "Nessun luogo trovato",
    "Retirer des favoris": "Togli dai preferiti", "Ajouter aux favoris": "Aggiungi ai preferiti",
    // ---- detail
    "N°": "N.", "Ouvrir sur ZiviConnect": "Apri su ZiviConnect", "Voir sur la carte": "Mostra sulla carta",
    "Traduire le texte de l’affectation dans la langue de l’interface avec le traducteur intégré du navigateur": "Traduci il testo dell’impiego nella lingua dell’interfaccia con il traduttore integrato del browser",
    "Traduire": "Traduci", "Durée minimale": "Durata minima", "Heures par semaine": "Ore settimanali",
    "Travail le week-end": "Lavoro nel fine settimana", "Travail de nuit": "Lavoro notturno",
    "Contrôle de réputation": "Verifica della reputazione", "Programme prioritaire": "Programma prioritario",
    "À l’étranger": "All’estero", "Ce qui la rend rare": "Cosa lo rende raro", "Tâches": "Compiti",
    "Compétences requises": "Conoscenze richieste", "Compétences souhaitées": "Conoscenze auspicate",
    "Cours du service civil": "Corsi del servizio civile", "Semaines de début disponibles": "Settimane d’inizio disponibili",
    "À propos de l’établissement": "Sull’istituto d’impiego", "Adresse de l’affectation": "Indirizzo dell’impiego",
    "Contact": "Contatto", "Comment postuler": "Come candidarsi",
    "Traduction enregistrée dans la base.": "Traduzione salvata nella banca dati.",
    "Voir l’original": "Mostra l’originale", "Aucune date publiée.": "Nessuna data pubblicata.",
    // ---- browser translator
    "Préparation du traducteur du navigateur…": "Preparazione del traduttore del browser…",
    "le traducteur intégré du navigateur ne répond pas": "il traduttore integrato del browser non risponde",
    "Téléchargement du modèle de traduction…": "Download del modello di traduzione…",
    "Téléchargement du modèle de traduction : {pct} %": "Download del modello di traduzione: {pct} %",
    "le navigateur ne sait pas traduire depuis {lang}": "il browser non sa tradurre dal {lang}",
    "Ce navigateur n’a pas de traducteur intégré utilisable par la page. Utilisez un Chrome ou Edge récent, ou faites un clic droit sur le texte → « Traduire ».": "Questo browser non ha un traduttore integrato utilizzabile dalla pagina. Usa un Chrome o Edge recente, oppure fai clic destro sul testo → «Traduci».",
    "Ce texte est déjà dans la langue de l’interface.": "Questo testo è già nella lingua dell’interfaccia.",
    "Le navigateur ne sait pas traduire depuis {lang}.": "Il browser non sa tradurre dal {lang}.",
    "Traduction en cours…": "Traduzione in corso…",
    "Traduit depuis {lang} par le traducteur intégré du navigateur.": "Tradotto dal {lang} dal traduttore integrato del browser.",
    "La traduction a échoué : {error}. Faites un clic droit sur le texte → « Traduire », ou utilisez un Chrome ou Edge récent.": "La traduzione non è riuscita: {error}. Fai clic destro sul testo → «Traduci», oppure usa un Chrome o Edge recente.",
    // ---- import
    "Envoi de {file} ({size} Mo)…": "Invio di {file} ({size} MB)…",
    "Échec de l’envoi : {error}": "Invio non riuscito: {error}", "Échec de l’import : {error}": "Importazione non riuscita: {error}",
    "{n} affectations chargées — {located} placées sur la carte, {weeks} avec des dates de début.": "{n} impieghi caricati — {located} sulla carta, {weeks} con date d’inizio.",
    "{n} Mo": "{n} MB", "{n} Ko": "{n} KB", "{n} Go": "{n} GB",
    // ---- database window
    "sur la carte": "sulla carta", "favoris": "preferiti", "textes traduits": "testi tradotti",
    "index IA": "indice IA", "pas construit": "non creato", "Fichier importé": "File importato",
    "Importé le": "Importato il", "Semaines de début": "Settimane d’inizio", "du {from} au {to}": "dal {from} al {to}",
    "Fichier de la base": "File della banca dati", "Copier": "Copia", "Taille": "Dimensione", "Copié": "Copiato",
    "Copie impossible": "Copia impossibile", "{n} vecteurs ({model})": "{n} vettori ({model})", "aucun": "nessuno",
    "Effacer tous les favoris ? C’est définitif.": "Cancellare tutti i preferiti? È definitivo.",
    "Supprimer les traductions en {lang} ? Les autres langues sont conservées.": "Eliminare le traduzioni in {lang}? Le altre lingue vengono conservate.",
    "Supprimer l’index IA de ce modèle (dans cette langue) ? Il faudra le reconstruire.": "Eliminare l’indice IA di questo modello (in questa lingua)? Dovrà essere ricreato.",
    // ---- translation
    "Rien à traduire": "Niente da tradurre",
    "{done} / {texts} textes · {adone} / {assignments} affectations entièrement traduites": "{done} / {texts} testi · {adone} / {assignments} impieghi tradotti per intero",
    "Toutes les affectations sont déjà en {lang}.": "Tutti gli impieghi sono già in {lang}.",
    "Reprendre la traduction": "Riprendi la traduzione", "Traduire la base en {lang}": "Traduci la banca dati in {lang}",
    "Ce navigateur n’a pas de traducteur intégré utilisable par la page : utilisez un Chrome ou Edge récent, ou Ollama.": "Questo browser non ha un traduttore integrato utilizzabile dalla pagina: usa un Chrome o Edge recente, oppure Ollama.",
    "{n} h": "{n} h", "{n} min": "{n} min",
    "Ce navigateur n’a pas de traducteur intégré : utilisez un Chrome ou Edge récent.": "Questo browser non ha un traduttore integrato: usa un Chrome o Edge recente.",
    "Le navigateur ne sait pas traduire depuis {lang} : ces textes seront gardés tels quels.": "Il browser non sa tradurre dal {lang}: questi testi resteranno invariati.",
    "le traducteur échoue sans arrêt ({error})": "il traduttore fallisce di continuo ({error})",
    "Traduction en cours… {rate} caractères/s · encore {eta} environ": "Traduzione in corso… {rate} caratteri/s · ancora circa {eta}",
    "dernière erreur : {error}": "ultimo errore: {error}",
    "« Reprendre la traduction » les réessaie.": "«Riprendi la traduzione» li riprova.",
    "En pause. « Reprendre la traduction » continue là où elle s’est arrêtée.": "In pausa. «Riprendi la traduzione» continua da dove si è fermata.",
    "Traduction terminée.": "Traduzione completata.",
    "La traduction s’est arrêtée : {error}. Ce qui est fait est enregistré ; vous pouvez reprendre.": "La traduzione si è fermata: {error}. Quanto fatto è salvato; puoi riprendere.",
    "Mise en pause après le texte en cours…": "Pausa dopo il testo in corso…",
    "La traduction tourne sur cet ordinateur, dans Ollama : vous pouvez fermer cette fenêtre, et même la page. Ce qui est traduit est enregistré au fur et à mesure ; « Traduire la base » reprend là où elle s’est arrêtée.": "La traduzione gira su questo computer, in Ollama: puoi chiudere questa finestra, e anche la pagina. Ciò che è tradotto viene salvato man mano; «Traduci la banca dati» riprende da dove si è fermata.",
    "La traduction se fait dans cette page : vous pouvez fermer cette fenêtre et continuer à chercher, mais gardez la page ouverte. Ce qui est traduit est enregistré au fur et à mesure ; « Traduire la base » reprend là où elle s’est arrêtée.": "La traduzione avviene in questa pagina: puoi chiudere questa finestra e continuare a cercare, ma tieni la pagina aperta. Ciò che è tradotto viene salvato man mano; «Traduci la banca dati» riprende da dove si è fermata.",
    "Ollama ne répond pas : démarrez-le ou installez-le ci-dessus.": "Ollama non risponde: avvialo o installalo qui sopra.",
    "Téléchargez d’abord le modèle {model} (bouton ci-dessus).": "Scarica prima il modello {model} (pulsante qui sopra).",
    "Traduction en {lang} avec {model}…": "Traduzione in {lang} con {model}…",
    "{rate} caractères/s · encore {eta} environ": "{rate} caratteri/s · ancora circa {eta}",
    "premier texte (le modèle se charge en mémoire)": "primo testo (il modello si carica in memoria)",
    "En pause{aside}. « Reprendre la traduction » continue là où elle s’est arrêtée.": "In pausa{aside}. «Riprendi la traduzione» continua da dove si è fermata.",
    "Traduction terminée{aside}.": "Traduzione completata{aside}.",
    "« Reprendre la traduction » réessaie les textes mis de côté.": "«Riprendi la traduzione» riprova i testi messi da parte.",
    "Suivi interrompu : {error}": "Monitoraggio interrotto: {error}",
    // ---- Ollama box
    "Impossible de joindre le serveur de l’application.": "Impossibile raggiungere il server dell’applicazione.",
    "Ollama n’est pas installé sur cet ordinateur. Installez-le (gratuit), puis revenez ici.": "Ollama non è installato su questo computer. Installalo (gratuito), poi torna qui.",
    "Télécharger Ollama ↗": "Scarica Ollama ↗", "Vérifier à nouveau": "Verifica di nuovo",
    "Ollama est installé mais ne tourne pas.": "Ollama è installato ma non è in esecuzione.", "Démarrer Ollama": "Avvia Ollama",
    "Téléchargement de {model} : {progress}…": "Download di {model}: {progress}…",
    "Ollama {version} · modèle {model} installé{size}.": "Ollama {version} · modello {model} installato{size}.",
    "Le téléchargement a échoué : {error}": "Il download non è riuscito: {error}",
    "Ollama {version} est prêt, mais le modèle {model} n’est pas installé.": "Ollama {version} è pronto, ma il modello {model} non è installato.",
    "Télécharger le modèle": "Scarica il modello",
    "Légende des couleurs": "Legenda dei colori", "Couleurs : domaine d’activité": "Colori: settore d’attività",
    "Bulle : plusieurs affectations proches, en parts par domaine ; le nombre au centre.": "Bolla: più impieghi vicini, in parti per settore; il numero al centro.",
    "Anneau doré : favori.": "Anello dorato: preferito.",
    "Filtrer : afficher les affectations avec cette valeur": "Filtra: mostra gli impieghi con questo valore",
    "Carte en plein écran": "Carta a schermo intero", "Quitter la carte en plein écran": "Esci dalla carta a schermo intero",
    "Libérer la mémoire et arrêter Ollama quand la page est fermée": "Libera la memoria e chiudi Ollama quando la pagina viene chiusa",
    "Environ une minute et demie après la fermeture de la dernière page, les modèles sont retirés de la mémoire et Ollama est arrêté, sauf pendant une traduction ou la construction d’un index. Il redémarre à la prochaine visite.": "Circa un minuto e mezzo dopo la chiusura dell’ultima pagina, i modelli vengono tolti dalla memoria e Ollama viene chiuso, tranne durante una traduzione o la creazione di un indice. Si riavvia alla visita successiva.",
    // ---- AI search
    "enregistré ({hint}) — laisser vide pour le conserver": "salvato ({hint}) — lascia vuoto per conservarlo",
    "texte original": "testo originale", "trop long ou trop lent": "troppo lungo o troppo lento",
    "Construction de l’index : {done} / {total} · lots de {batch}": "Creazione dell’indice: {done} / {total} · lotti da {batch}",
    "La dernière construction s’est arrêtée : {error}. Ce qui est fait est gardé ; « Construire l’index » reprend.": "L’ultima creazione si è fermata: {error}. Quanto fatto è conservato; «Crea l’indice» riprende.",
    "Arrêtée à {done} / {total}. « Construire l’index » reprend là où elle s’est arrêtée.": "Fermata a {done} / {total}. «Crea l’indice» riprende da dove si è fermata.",
    "Index IA prêt : {n} affectations ({model}){skipped}. Choisissez « IA » ou « Les deux » à côté du champ de recherche.": "Indice IA pronto: {n} impieghi ({model}){skipped}. Scegli «IA» o «Entrambi» accanto al campo di ricerca.",
    "Pas encore d’index IA pour ce modèle dans cette langue.": "Ancora nessun indice IA per questo modello in questa lingua.",
    "{count} : « Construire l’index » s’en charge.": "{count}: «Crea l’indice» se ne occupa.",
    "Utilise la traduction dans la langue de l’interface quand elle existe (Traduction), sinon le texte original : {have} affectations sur {total} sont en {lang} ({native} d’origine, {translated} traduites). Chaque langue garde son propre index.": "Usa la traduzione nella lingua dell’interfaccia quando esiste (Traduzione), altrimenti il testo originale: {have} impieghi su {total} sono in {lang} ({native} in originale, {translated} tradotti). Ogni lingua mantiene il proprio indice.",
    "Index déjà construits : {list}.": "Indici già creati: {list}.",
    "Connecté. Le modèle renvoie des vecteurs de {n} dimensions.": "Connesso. Il modello restituisce vettori di {n} dimensioni.",
    "Tout reconstruire ? Les vecteurs de ce modèle (dans cette langue) seront recalculés pour toutes les affectations.": "Ricreare tutto? I vettori di questo modello (in questa lingua) saranno ricalcolati per tutti gli impieghi.",
    "Arrêt après le lot en cours…": "Arresto dopo il lotto in corso…",
    // ---- plurals
    "{n} semaine": "{n} settimana", "{n} semaines": "{n} settimane",
    "{n} lieu sur la carte": "{n} luogo sulla carta", "{n} lieux sur la carte": "{n} luoghi sulla carta",
    "{n} résultat sans localisation": "{n} risultato senza posizione", "{n} résultats sans localisation": "{n} risultati senza posizione",
    "{n} affectation": "{n} impiego", "{n} affectations": "{n} impieghi",
    "{n} favori": "{n} preferito", "{n} favoris": "{n} preferiti",
    "{n} texte traduit": "{n} testo tradotto", "{n} textes traduits": "{n} testi tradotti",
    "{n} texte mis de côté": "{n} testo messo da parte", "{n} textes mis de côté": "{n} testi messi da parte",
    "{n} texte n’a pas pu être traduit": "{n} testo non ha potuto essere tradotto", "{n} textes n’ont pas pu être traduits": "{n} testi non hanno potuto essere tradotti",
    "{n} texte ignoré": "{n} testo saltato", "{n} textes ignorés": "{n} testi saltati",
    "{n} affectation est à calculer": "{n} impiego da calcolare", "{n} affectations sont à calculer": "{n} impieghi da calcolare",
    // ---- values and names
    "Domaine": "Settore", "Horaire": "Orario", "Canton": "Cantone", "Tâche": "Compito",
    "Allemand": "Tedesco", "Français": "Francese", "Italien": "Italiano",
    "Base de données": "Banca dati", "Traduction": "Traduzione", "Recherche IA": "Ricerca IA",
    "allemand": "tedesco", "français": "francese", "italien": "italiano", "anglais": "inglese",
    "moins de 40 h": "meno di 40 h", "40 à 42,5 h": "da 40 a 42,5 h", "43 à 45 h": "da 43 a 45 h", "plus de 45 h": "più di 45 h",
    // ---- static page
    "Accueil Better Zivi": "Pagina iniziale Better Zivi",
    "Rechercher des tâches, établissements, lieux…": "Cerca compiti, istituti, luoghi…",
    "Recherche avancée": "Ricerca avanzata", "Effacer": "Cancella", "Tous ces mots": "Tutte queste parole",
    "jardin cuisine": "giardino cucina", "Cette expression exacte": "Questa espressione esatta", "aide en cuisine": "aiuto in cucina",
    "Au moins un de ces mots": "Almeno una di queste parole", "chien chat cheval": "cane gatto cavallo",
    "Aucun de ces mots": "Nessuna di queste parole", "nuit week-end": "notte fine settimana", "Requête :": "Ricerca:",
    "Fermer": "Chiudi", "Mode de recherche": "Modalità di ricerca", "Mots-clés": "Parole chiave",
    "Recherche par le sens, nécessite un jeton IA": "Ricerca per significato, richiede un token IA", "IA": "IA",
    "Mots-clés et IA combinés": "Parole chiave e IA combinate", "Les deux": "Entrambi",
    "Afficher uniquement les favoris": "Mostra solo i preferiti", "Favoris": "Preferiti",
    "Base de données : import, traduction, recherche IA": "Banca dati: importazione, traduzione, ricerca IA",
    "Télécharger les résultats actuels en CSV": "Scarica i risultati attuali in CSV", "Exporter CSV": "Esporta CSV",
    "Panneaux": "Pannelli", "Filtres": "Filtri", "Résultats": "Risultati", "Carte": "Carta", "Tout effacer": "Cancella tutto",
    "Période": "Periodo", "Du": "Dal", "Au": "Al",
    "Début dans cette période, pour au moins": "Inizio in questo periodo, per almeno",
    "semaines consécutives": "settimane consecutive", "Disponible chaque semaine de la période": "Disponibile ogni settimana del periodo",
    "Autour de lieux": "Intorno a luoghi", "Localité ou NPA, p. ex. Lausanne ou 3800": "Località o NPA, p. es. Lugano o 6900",
    "Rayon": "Raggio", "Ajouter un lieu en cliquant sur la carte": "Aggiungi un luogo cliccando sulla carta",
    "Seulement ce qui est visible sur la carte": "Solo ciò che è visibile sulla carta", "Conditions": "Condizioni",
    "Logement offert": "Alloggio offerto", "Repas offerts": "Vitto offerto",
    "Aucun cours du service civil requis": "Nessun corso del servizio civile richiesto", "Durée et horaires": "Durata e orari",
    "Durée min. de": "Durata min. da", "semaines": "settimane", "à": "a", "Heures/semaine de": "Ore/settimana da",
    "Types de tâches": "Tipi di compiti", "au moins un": "almeno uno", "tous": "tutti", "Plus": "Altro",
    "La langue est déduite du texte de la description.": "La lingua è dedotta dal testo della descrizione.",
    "Chargement…": "Caricamento…",
    "Trier par rareté : d’abord les affectations aux caractéristiques que peu d’autres partagent": "Ordina per rarità: prima gli impieghi con caratteristiche che pochi altri hanno",
    "✨ Raretés": "✨ Rarità", "Trier": "Ordina", "Pertinence": "Pertinenza", "Distance": "Distanza",
    "Début le plus tôt": "Inizio più vicino", "Durée minimale la plus courte": "Durata minima più breve",
    "Plus récentes": "Più recenti", "Titre": "Titolo", "Rareté": "Rarità", "Raretés sélectionnées": "Rarità scelte",
    "Afficher plus": "Mostra altri", "Chargement des données": "Caricamento dei dati", "Envoi…": "Invio…",
    "Sections": "Sezioni", "Réglages": "Impostazioni", "Données": "Dati",
    "Charger un export JSON…": "Carica un export JSON…",
    "Remplace les affectations. Les favoris, les traductions, l’index IA et les lieux déjà trouvés sont conservés.": "Sostituisce gli impieghi. Preferiti, traduzioni, indice IA e luoghi già trovati vengono conservati.",
    "Options avancées": "Opzioni avanzate", "Ces actions sont définitives.": "Queste azioni sono definitive.",
    "Effacer les favoris": "Cancella i preferiti", "Traductions": "Traduzioni", "Supprimer les traductions": "Elimina le traduzioni",
    "Index IA": "Indice IA", "Supprimer l’index IA": "Elimina l’indice IA",
    "Traduit dans la langue de l’interface les affectations rédigées dans une autre langue. Le modèle tourne sur cet ordinateur : gratuit, et rien n’est envoyé sur Internet. Chaque langue garde ses propres traductions.": "Traduce nella lingua dell’interfaccia gli impieghi redatti in un’altra lingua. Il modello gira su questo computer: gratuito, e nulla viene inviato su Internet. Ogni lingua mantiene le proprie traduzioni.",
    "Moteur de traduction": "Motore di traduzione", "recommandé": "consigliato",
    "Modèle de traduction local, rapide ; continue même si la page est fermée.": "Modello di traduzione locale, veloce; continua anche se la pagina è chiusa.",
    "Navigateur": "Browser", "Traducteur intégré de Chrome ou Edge, sans installation, mais lent.": "Traduttore integrato di Chrome o Edge, senza installazione, ma lento.",
    "Modèle": "Modello", "Traduire la base": "Traduci la banca dati", "Pause": "Pausa", "Affichage et recherche": "Visualizzazione e ricerca",
    "Afficher les affectations dans la langue de l’interface": "Mostra gli impieghi nella lingua dell’interfaccia",
    "Titres, tâches et descriptions traduits dans la liste, la carte et la fiche. La fiche garde un bouton « Voir l’original ».": "Titoli, compiti e descrizioni tradotti nella lista, sulla carta e nella scheda. La scheda mantiene un pulsante «Mostra l’originale».",
    "Chercher aussi dans le texte original": "Cerca anche nel testo originale",
    "Désactivé : seuls les textes dans la langue de l’interface (originaux ou traduits) sont cherchés, et une affectation pas encore traduite ne ressort pas.": "Disattivato: vengono cercati solo i testi nella lingua dell’interfaccia (originali o tradotti), e un impiego non ancora tradotto non compare.",
    "Trouve des affectations par le sens (« travailler avec des animaux en plein air ») grâce aux embeddings de texte. Fonctionne avec tout service compatible OpenAI, ou un Ollama local sans jeton. Le jeton reste dans la mémoire du serveur local et n’est jamais écrit sur le disque.": "Trova impieghi per significato («lavorare con gli animali all’aperto») grazie agli embedding di testo. Funziona con qualsiasi servizio compatibile OpenAI, o un Ollama locale senza token. Il token resta nella memoria del server locale e non viene mai scritto sul disco.",
    "Index": "Indice", "Construire l’index dans la langue de l’interface": "Crea l’indice nella lingua dell’interfaccia",
    "Construire l’index": "Crea l’indice", "Arrêter": "Ferma",
    "Efface les vecteurs de ce modèle et recalcule toutes les affectations": "Cancella i vettori di questo modello e ricalcola tutti gli impieghi",
    "Tout reconstruire": "Ricrea tutto",
    "« Construire » ne calcule que les affectations nouvelles ou modifiées et reprend après un arrêt. Avec un modèle local, les lots trop lents sont réessayés puis découpés.": "«Crea» calcola solo gli impieghi nuovi o modificati e riprende dopo un arresto. Con un modello locale, i lotti troppo lenti vengono riprovati e poi suddivisi.",
    "Service": "Servizio", "Fournisseur": "Fornitore",
    "Ollama sur cet ordinateur — nomic-embed-text": "Ollama su questo computer — nomic-embed-text", "Personnalisé": "Personalizzato",
    "URL de base de l’API": "URL di base dell’API", "Jeton": "Token", "Mémoriser le jeton dans ce navigateur": "Memorizza il token in questo browser",
    "Enregistrer et tester la connexion": "Salva e prova la connessione", "Résultats retenus par la recherche IA": "Risultati conservati dalla ricerca IA",
    "Les 50 plus proches": "I 50 più vicini", "Les 100 plus proches": "I 100 più vicini", "Les 200 plus proches": "I 200 più vicini",
    "Les 500 plus proches": "I 500 più vicini", "Toutes, classées par proximité": "Tutti, ordinati per vicinanza",
    "3,3 Go — rapide, recommandé": "3,3 GB — veloce, consigliato",
    "8,1 Go — meilleure qualité, demande ~16 Go de mémoire": "8,1 GB — qualità migliore, richiede ~16 GB di memoria",
    "17 Go — la meilleure, très lourd": "17 GB — il migliore, molto pesante",
  },
};

/* Blocks translated as whole HTML (they mix text, <code> and <i>): data-i18n-html="key" */
const HTML = {
  de: {
    qhint: `<b>So suchen Sie</b>
        <dl>
          <dt><code>Garten Küche</code></dt><dd>alle Wörter müssen vorkommen</dd>
          <dt><code>pflege</code></dt><dd>Wortanfang: findet auch <i>Pflegeheim</i>, <i>Pflegefachperson</i></dd>
          <dt><code>"Mithilfe in der Küche"</code></dt><dd>genauer Ausdruck, Wörter in dieser Reihenfolge</dd>
          <dt><code>-Nacht</code></dt><dd>schliesst Einsätze aus, die dieses Wort enthalten</dd>
          <dt><code>Hund OR Katze</code></dt><dd>das eine oder das andere (<code>OR</code> in Grossbuchstaben)</dd>
        </dl>
        <p>Akzente sowie Gross- und Kleinschreibung werden ignoriert. Durchsucht werden Titel, Aufgaben,
          Kenntnisse, Einsatzbetrieb, Ort, Kurse, Kontaktperson (Name, Funktion, E-Mail, Telefon),
          Adressen und die Nummer des Einsatzes. Im Modus <b>KI</b> wird der Satz nach seiner Bedeutung
          verstanden, und diese Operatoren gelten nicht.</p>
        <p class="q-hint-foot">▾ öffnet die erweiterte Suche, um diese Optionen Feld für Feld auszufüllen.</p>`,
    advnote: `Ein Wort findet auch die Wörter, die gleich beginnen (<i>pflege</i> → <i>Pflegeheim</i>).
          Akzente sowie Gross- und Kleinschreibung werden ignoriert.`,
  },
  it: {
    qhint: `<b>Come cercare</b>
        <dl>
          <dt><code>giardino cucina</code></dt><dd>tutte le parole devono comparire</dd>
          <dt><code>pflege</code></dt><dd>inizio di parola: trova anche <i>Pflegeheim</i>, <i>Pflegefachperson</i></dd>
          <dt><code>"aiuto in cucina"</code></dt><dd>espressione esatta, parole in quest’ordine</dd>
          <dt><code>-notte</code></dt><dd>esclude gli impieghi che contengono questa parola</dd>
          <dt><code>cane OR gatto</code></dt><dd>l’uno o l’altro (<code>OR</code> in maiuscolo)</dd>
        </dl>
        <p>Accenti e maiuscole vengono ignorati. La ricerca riguarda titolo, compiti, conoscenze,
          istituto d’impiego, luogo, corsi, persona di contatto (nome, funzione, e-mail, telefono),
          indirizzi e numero dell’impiego. In modalità <b>IA</b> la frase è compresa per il suo significato
          e questi operatori non si applicano.</p>
        <p class="q-hint-foot">▾ apre la ricerca avanzata per compilare queste opzioni campo per campo.</p>`,
    advnote: `Una parola trova anche le parole che iniziano allo stesso modo (<i>pflege</i> → <i>Pflegeheim</i>).
          Accenti e maiuscole ignorati.`,
  },
};

/* Server messages: {key, params, text} (src/zivi/messages.py has the French originals) */
const SERVER = {
  fr: {},
  de: {
    no_data: "Keine Daten: Laden Sie zuerst eine JSON-Datei.",
    ai_not_configured: "Die KI-Suche ist nicht eingerichtet: Fügen Sie unter Datenbank → KI-Suche ein Token hinzu. Stattdessen wird die Stichwortsuche verwendet.",
    ai_no_index: "Der KI-Index ist noch nicht erstellt: Datenbank → KI-Suche → «Index erstellen». Stattdessen wird die Stichwortsuche verwendet.",
    ai_failed: "Die KI-Suche ist fehlgeschlagen ({error}); stattdessen wird die Stichwortsuche verwendet.",
    unknown_assignment: "Unbekannter Einsatz", import_running: "Ein Import läuft bereits",
    ai_setup_first: "Geben Sie zuerst eine Basis-URL, ein Modell und ein Token an",
    ai_building: "Erstellung läuft bereits", ai_stop_first: "Erstellung läuft: Halten Sie sie zuerst an",
    tr_running: "Die Übersetzung läuft bereits", ollama_missing: "Ollama ist auf diesem Computer nicht installiert",
    model_required: "Geben Sie ein Modell an",
    import_start: "Start", import_failed: "Import fehlgeschlagen", import_read: "JSON wird gelesen",
    import_parse: "Einträge werden gelesen {i}/{n}", import_write: "{n} Einsätze werden gespeichert",
    import_geocode: "Geolokalisierung {i}/{n}", import_fts: "Volltextindex wird erstellt",
    import_done: "Fertig: {n} Einsätze, {located} lokalisiert", import_empty: "Keine Einsätze in dieser JSON-Datei gefunden.",
    api_error: "Fehler der Embedding-API {status}: {body}", no_response: "keine Antwort des Dienstes ({kind})",
    index_other_model: "der Index wurde mit einem anderen Modell erstellt ({model}); erstellen Sie ihn unter Datenbank → KI-Suche neu",
    retry: "neuer Versuch {attempt}/{of}: {error}",
    ollama_no_response: "keine Antwort von Ollama ({kind})", ollama_http: "Ollama hat {status} geantwortet: {body}",
    ollama_model_missing: "das Modell {model} ist in Ollama nicht installiert", empty_response: "leere Antwort",
    ollama_failing: "Ollama scheitert ständig ({error})",
  },
  it: {
    no_data: "Nessun dato: carica prima un file JSON.",
    ai_not_configured: "La ricerca IA non è configurata: aggiungi un token in Banca dati → Ricerca IA. Al suo posto viene usata la ricerca per parole chiave.",
    ai_no_index: "L’indice IA non è ancora stato creato: Banca dati → Ricerca IA → «Crea l’indice». Al suo posto viene usata la ricerca per parole chiave.",
    ai_failed: "La ricerca IA non è riuscita ({error}); al suo posto viene usata la ricerca per parole chiave.",
    unknown_assignment: "Impiego sconosciuto", import_running: "Un’importazione è già in corso",
    ai_setup_first: "Indica prima un URL di base, un modello e un token",
    ai_building: "Creazione già in corso", ai_stop_first: "Creazione in corso: fermala prima",
    tr_running: "La traduzione è già in corso", ollama_missing: "Ollama non è installato su questo computer",
    model_required: "Indica un modello",
    import_start: "Avvio", import_failed: "Importazione non riuscita", import_read: "Lettura del JSON",
    import_parse: "Analisi delle schede {i}/{n}", import_write: "Scrittura di {n} impieghi",
    import_geocode: "Geolocalizzazione {i}/{n}", import_fts: "Creazione dell’indice di testo",
    import_done: "Completato: {n} impieghi, {located} localizzati", import_empty: "Nessun impiego trovato in questo file JSON.",
    api_error: "Errore dell’API di embedding {status}: {body}", no_response: "nessuna risposta dal servizio ({kind})",
    index_other_model: "l’indice è stato creato con un altro modello ({model}); ricrealo in Banca dati → Ricerca IA",
    retry: "nuovo tentativo {attempt}/{of}: {error}",
    ollama_no_response: "nessuna risposta da Ollama ({kind})", ollama_http: "Ollama ha risposto {status}: {body}",
    ollama_model_missing: "il modello {model} non è installato in Ollama", empty_response: "risposta vuota",
    ollama_failing: "Ollama fallisce di continuo ({error})",
  },
};

const fill = (s, params) => (params ? s.replace(/\{(\w+)\}/g, (_, k) => (params[k] ?? "")) : s);
/* t("Texte français", {n: 2}) in the interface language */
function t(fr, params) {
  const s = LANG === "fr" ? fr : (I18N[LANG]?.[fr] ?? fr);
  return fill(s, params);
}
/* tn(3, "{n} affectation", "{n} affectations"): the number formatted for the language */
function tn(n, one, many, params) {
  return t(n === 1 ? one : many, { ...params, n: Number(n).toLocaleString(LOCALE[LANG]) });
}
/* a server message {key, params, text} (params may be messages too), or a plain string */
function fmtMsg(m) {
  if (m == null) return "";
  if (typeof m !== "object") return String(m);
  if (m.key === "raw" || !m.key) return m.params?.text ?? m.text ?? "";
  const tpl = SERVER[LANG]?.[m.key];
  if (!tpl) return m.text ?? m.key;
  const params = {};
  for (const [k, v] of Object.entries(m.params || {})) params[k] = typeof v === "object" ? fmtMsg(v) : v;
  return fill(tpl, params);
}

/* Data labels (sector, task types, working hours…): French label -> {de, it}, from the
   coded values of the export (GET /api/labels). */
let LABELS = {};
async function loadLabels() {
  try { LABELS = await (await fetch("/api/labels")).json(); } catch { LABELS = {}; }
}
const dataLabel = (v) => (LANG === "fr" || v == null ? v : (LABELS[v]?.[LANG] ?? v));

/* ------------------------------------------------------------------ the static page */
const ATTRS = ["title", "placeholder", "aria-label"];
let staticTexts = null; // [[node, original French]] recorded once, before any change
function recordStatic() {
  staticTexts = { nodes: [], attrs: [], html: [] };
  const skip = (el) => el.closest("script, style, svg, code, [data-i18n-skip], [data-i18n-html]");
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (n.nodeValue.trim() && !skip(n.parentElement)) staticTexts.nodes.push([n, n.nodeValue]);
  }
  for (const el of document.body.querySelectorAll(ATTRS.map((a) => `[${a}]`).join(","))) {
    if (el.closest("[data-i18n-skip]")) continue;
    for (const a of ATTRS) if (el.hasAttribute(a)) staticTexts.attrs.push([el, a, el.getAttribute(a)]);
  }
  for (const el of document.querySelectorAll("[data-i18n-html]")) staticTexts.html.push([el, el.innerHTML]);
  staticTexts.title = document.title;
}
/* keeps the spaces around a text node while translating its content */
function translateKeepingSpace(orig) {
  const core = orig.replace(/\s+/g, " ").trim();
  const done = t(core);
  if (done === core) return orig;
  const lead = orig.match(/^\s*/)[0] ? " " : "", trail = orig.match(/\s*$/)[0] ? " " : "";
  return lead + done + trail;
}
function setLang(lang) {
  if (!LOCALE[lang]) return;
  LANG = lang;
  try { localStorage.setItem("zivi.lang", JSON.stringify(lang)); } catch { /* private mode */ }
  document.documentElement.lang = lang;
  if (!staticTexts) recordStatic();
  for (const [node, orig] of staticTexts.nodes) if (node.isConnected) node.nodeValue = translateKeepingSpace(orig);
  for (const [el, a, orig] of staticTexts.attrs) if (el.isConnected) el.setAttribute(a, t(orig.replace(/\s+/g, " ").trim()));
  for (const [el, orig] of staticTexts.html) el.innerHTML = lang === "fr" ? orig : (HTML[lang]?.[el.dataset.i18nHtml] ?? orig);
  document.title = t(staticTexts.title);
}
