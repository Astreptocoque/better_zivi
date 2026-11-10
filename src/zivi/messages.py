"""Messages the server sends to the page: a code + parameters, translated by the page.

Every message also carries its French text, used by the command line and as a fallback
when the page does not know a code. Errors raised with ZiviError carry such a message.
"""

from __future__ import annotations

from typing import Any

FR = {
    # search
    "no_data": "Aucune donnée : chargez d'abord un fichier JSON.",
    "ai_not_configured": "La recherche IA n'est pas configurée : ajoutez un jeton dans "
                         "Base de données → Recherche IA. Recherche par mots-clés utilisée "
                         "à la place.",
    "ai_no_index": "L'index IA n'est pas encore construit : Base de données → Recherche IA → "
                   "« Construire l'index ». Recherche par mots-clés utilisée à la place.",
    "ai_failed": "La recherche IA a échoué ({error}) ; recherche par mots-clés utilisée à la "
                 "place.",
    # API
    "unknown_assignment": "Affectation inconnue",
    "import_running": "Un import est déjà en cours",
    "ai_setup_first": "Indiquez d'abord une URL de base, un modèle et un jeton",
    "ai_building": "Construction déjà en cours",
    "ai_stop_first": "Construction en cours : arrêtez-la d'abord",
    "tr_running": "La traduction est déjà en cours",
    "ollama_missing": "Ollama n'est pas installé sur cet ordinateur",
    "model_required": "Indiquez un modèle",
    "import_bad_file": "Ce fichier n'est pas une sauvegarde de Better Zivi de ce type.",
    "import_empty_file": "Ce fichier ne contient rien d'utilisable.",
    "import_unreadable": "Fichier illisible (JSON ou JSON compressé gzip attendu).",
    # import
    "import_start": "Démarrage",
    "import_failed": "Échec de l'import",
    "import_read": "Lecture du JSON",
    "import_parse": "Analyse des fiches {i}/{n}",
    "import_write": "Écriture de {n} affectations",
    "import_geocode": "Géolocalisation {i}/{n}",
    "import_fts": "Construction de l'index plein texte",
    "import_done": "Terminé : {n} affectations, {located} localisées",
    "import_empty": "Aucune affectation trouvée dans ce fichier JSON.",
    # AI index
    "api_error": "Erreur de l'API d'embeddings {status} : {body}",
    "no_response": "pas de réponse du service ({kind})",
    "index_other_model": "l'index a été construit avec un autre modèle ({model}) ; "
                         "reconstruisez-le dans Base de données → Recherche IA",
    "retry": "nouvel essai {attempt}/{of} : {error}",
    # Ollama
    "ollama_no_response": "pas de réponse d'Ollama ({kind})",
    "ollama_http": "Ollama a répondu {status} : {body}",
    "ollama_model_missing": "le modèle {model} n'est pas installé dans Ollama",
    "empty_response": "réponse vide",
    "ollama_failing": "Ollama échoue sans arrêt ({error})",
}


def text_of(m: Any) -> str:
    return m["text"] if isinstance(m, dict) and "text" in m else str(m)


def msg(key: str, **params: Any) -> dict:
    """{"key", "params", "text"}; a parameter may itself be a message."""
    flat = {k: text_of(v) for k, v in params.items()}
    template = FR.get(key)
    text = template.format(**flat) if template else str(params.get("text", key))
    return {"key": key if template else "raw", "params": params, "text": text}


def raw(text: str) -> dict:
    """A message that is not translatable (an error text from another program)."""
    return {"key": "raw", "params": {"text": text}, "text": text}


class ZiviError(RuntimeError):
    """An error with a translatable message: ZiviError("ollama_http", status=500, body=…).
    A first argument that is not a known code is kept as plain text."""

    def __init__(self, key: str, **params: Any):
        self.msg = msg(key, **params) if key in FR else raw(key)
        super().__init__(self.msg["text"])


def as_msg(exc: BaseException) -> dict:
    return exc.msg if isinstance(exc, ZiviError) else raw(str(exc))
