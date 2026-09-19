"""Settings that can be edited in the admin GUI (/admin/config) instead of .env.

.env stays the baseline. A value typed in the GUI is an *override* stored in
the database; clearing it goes back to following .env. Two services own
settings: the scheduler (applies overrides itself, see scheduler/
runtime_settings.py) and this service (reads them at the point of use).

WHAT MAY BE EDITED HERE IS A SECURITY DECISION. Only plain tuning knobs live in
SPECS. Secrets and anything that decides *where mail goes, what is read or how
services find each other* stay console-only (CONSOLE_ONLY below), so a stolen
admin session can change how often things run but cannot read or redirect
credentials or mail. A key that is not in SPECS is never stored or served.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy.orm import Session

from app import models
from app.config import settings

Kind = Literal["int", "float", "bool", "choice"]
Owner = Literal["scheduler", "scoring"]


@dataclass(frozen=True)
class Spec:
    key: str  # the env var name
    owner: Owner
    kind: Kind
    group: str
    label: str
    help: str
    default: Any  # code default; only shown when the .env value is not known (yet)
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    attr: str | None = None  # attribute on app.config.settings, for owner == "scoring"
    note: str = ""  # extra remark shown next to the field (e.g. that a job is rescheduled)


_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

SPECS: tuple[Spec, ...] = (
    # --- scoren en ophalen ---
    Spec("SCORE_REQUEST_DELAY_SECONDS", "scheduler", "float", "Scoren en ophalen", "Pauze tussen scoring-aanroepen (s)",
         "Minimale afstand tussen POST /score-aanroepen, over alle jobs samen. 21 voor Voyage's gratis tier "
         "(3 aanroepen/min); 0 met een betaalmethode.", 0.0, 0, 300),
    Spec("INGEST_INTERVAL_MINUTES", "scheduler", "int", "Scoren en ophalen", "Ingest-interval (minuten)",
         "Hoe vaak feeds worden opgehaald en gescoord.", 30, 1, 1440, note="plant de job opnieuw in"),
    Spec("MAX_NEW_ENTRIES_PER_SOURCE", "scheduler", "int", "Scoren en ophalen", "Max. nieuwe items per bron per run",
         "Alleen de nieuwste N ongeziene items per bron worden gescoord; oudere achterstand wordt als gezien "
         "gemarkeerd. 0 = onbeperkt.", 10, 0, 500),
    # --- digest ---
    Spec("DIGEST_DRY_RUN", "scheduler", "bool", "Digest", "Dry-run (niet echt mailen)",
         "Aan: de digest wordt alleen in de scheduler-logs gezet en er wordt niets gemarkeerd. Uit: er wordt "
         "echt gemaild.", True),
    Spec("DIGEST_LOOKBACK_DAYS", "scheduler", "int", "Digest", "Digest: kijkvenster (dagen)",
         "De dagelijkse digest kiest uit nog niet gemailde items van de laatste N dagen.", 3, 1, 30),
    Spec("MANUAL_DIGEST_LOOKBACK_DAYS", "scheduler", "int", "Digest", "'Verstuur nu': kijkvenster (dagen)",
         "Kijkvenster van de knop 'Verstuur digest nu'.", 7, 1, 90),
    # --- discovery ---
    Spec("DISCOVERY_DAY", "scheduler", "choice", "Wekelijkse discovery", "Discovery: dag",
         "Dag van de week waarop de discovery-sweep draait.", "mon", choices=_DAYS, note="plant de job opnieuw in"),
    Spec("DISCOVERY_HOUR", "scheduler", "int", "Wekelijkse discovery", "Discovery: uur (0-23)",
         "Uur waarop de discovery-sweep draait (tijd van de scheduler-container, UTC).", 8, 0, 23,
         note="plant de job opnieuw in"),
    Spec("DISCOVERY_LOOKBACK_DAYS", "scheduler", "int", "Wekelijkse discovery", "Discovery: terugkijken (dagen)",
         "Hoever terug 'interessant'-items worden gebruikt om zoektermen af te leiden.", 30, 1, 365),
    Spec("DISCOVERY_MAX_SEARCH_TERMS", "scheduler", "int", "Wekelijkse discovery", "Discovery: max. zoektermen",
         "Aantal zoektermen per sweep.", 5, 1, 20),
    Spec("DISCOVERY_RESULTS_PER_TERM", "scheduler", "int", "Wekelijkse discovery", "Discovery: resultaten per zoekterm",
         "Aantal zoekresultaten per term.", 5, 1, 20),
    # --- mailbox ---
    Spec("MAILBOX_INGEST_ENABLED", "scheduler", "bool", "Mailbox-ingest (nieuwsbrieven)", "Mailbox-ingest aan",
         "Leest ongelezen nieuwsbrieven uit de digest-mailbox. Vereist Mail.Read (admin consent) op de "
         "Graph-appregistratie.", False),
    Spec("MAILBOX_INGEST_MAX_MESSAGES", "scheduler", "int", "Mailbox-ingest (nieuwsbrieven)", "Max. berichten per run",
         "Maximum aantal ongelezen berichten dat per run wordt verwerkt.", 25, 1, 100),
    Spec("MAILBOX_INGEST_HOUR", "scheduler", "int", "Mailbox-ingest (nieuwsbrieven)", "Mailbox-ingest: uur (0-23)",
         "Uur waarop de mailbox-ingest draait (UTC).", 6, 0, 23, note="plant de job opnieuw in"),
    # --- bronnenbeheer (deze service) ---
    Spec("SOURCE_ACTIVATION_WINDOW", "scoring", "int", "Bronnenbeheer: instroom", "Instroom: aantal recente items",
         "Een kandidaat-bron wordt beoordeeld op zijn laatste N items.", 5, 1, 100, attr="source_activation_window"),
    Spec("SOURCE_ACTIVATION_MIN_HIGH_SCORE", "scoring", "int", "Bronnenbeheer: instroom",
         "Instroom: minimaal aantal hoge scores",
         "Zoveel van die items moeten boven de drempel scoren om actief te worden. Niet groter dan het aantal "
         "recente items.", 3, 1, 100, attr="source_activation_min_high_score"),
    Spec("SOURCE_ACTIVATION_SCORE_THRESHOLD", "scoring", "float", "Bronnenbeheer: instroom", "Instroom: scoredrempel (0-1)",
         "Score waarboven een item als 'hoog' telt.", 0.6, 0, 1, attr="source_activation_score_threshold"),
    Spec("SOURCE_DEACTIVATION_WINDOW", "scoring", "int", "Bronnenbeheer: krimp", "Krimp: aantal recente items",
         "Een actieve bron wordt beoordeeld op zijn laatste N items.", 10, 1, 100, attr="source_deactivation_window"),
    Spec("SOURCE_DEACTIVATION_MIN_NEGATIVE", "scoring", "int", "Bronnenbeheer: krimp", "Krimp: minimaal aantal 👎",
         "Zoveel van die items met 👎 deactiveren de bron. Niet groter dan het aantal recente items.", 8, 1, 100,
         attr="source_deactivation_min_negative"),
    Spec("SOURCE_DEACTIVATION_AVG_SCORE_THRESHOLD", "scoring", "float", "Bronnenbeheer: krimp",
         "Krimp: gemiddelde-scoredrempel (0-1)",
         "Zakt de gemiddelde score van een bron hieronder, dan wordt hij gedeactiveerd.", 0.3, 0, 1,
         attr="source_deactivation_avg_score_threshold"),
)

_BY_KEY = {spec.key: spec for spec in SPECS}


@dataclass(frozen=True)
class ConsoleOnly:
    name: str
    reason: str


# Shown (name and reason only, never a value) so it is clear what exists and
# why it is not here. Changing these takes a .env edit on the VM.
CONSOLE_ONLY: tuple[ConsoleOnly, ...] = (
    ConsoleOnly("VOYAGE_API_KEY", "geheim (API-sleutel)"),
    ConsoleOnly("GRAPH_TENANT_ID / GRAPH_CLIENT_ID / GRAPH_CLIENT_SECRET", "geheim (Microsoft Graph-inloggegevens)"),
    ConsoleOnly("SEARCH_API_KEY", "geheim (API-sleutel); SEARCH_PROVIDER hoort erbij"),
    ConsoleOnly("ADMIN_USERNAME / ADMIN_PASSWORD_HASH / SESSION_SECRET_KEY", "geheim (toegang tot deze pagina zelf)"),
    ConsoleOnly("POSTGRES_USER / POSTGRES_PASSWORD / DATABASE_URL", "geheim (database-toegang)"),
    ConsoleOnly("DIGEST_MAILBOX", "bepaalt namens welke mailbox er verstuurd wordt (Graph-rechten)"),
    ConsoleOnly("DIGEST_TO_EMAIL", "bepaalt waar de digest heen gaat"),
    ConsoleOnly("MAILBOX_INGEST_FOLDER", "bepaalt welke map van de mailbox wordt gelezen"),
    ConsoleOnly("FEEDBACK_BASE_URL", "de 👍/👎-links in de mail verwijzen hiernaartoe (misbruik: phishing)"),
    ConsoleOnly("VOYAGE_MODEL / EMBEDDING_PROVIDER / EMBEDDING_DIM",
                "een ander model maakt bestaande embeddings onvergelijkbaar en breekt de vector-kolom"),
    ConsoleOnly("DEFAULT_USER_ID", "koppelt alle feedback en items aan één gebruiker"),
    ConsoleOnly("DIGEST_HOUR / DIGEST_TOP_N", "staan al op de pagina 'Digest-instellingen'"),
    ConsoleOnly("SCORING_SERVICE_URL / SCHEDULER_URL / TRIGGER_SERVER_PORT / BIND_ADDRESS",
                "netwerkopzet tussen de containers"),
    ConsoleOnly("SETTINGS_SYNC_INTERVAL_SECONDS / SEEN_ITEMS_PATH", "interne werking van de scheduler"),
)


def spec_for(key: str) -> Spec | None:
    return _BY_KEY.get(key)


# --- parsing -------------------------------------------------------------


def parse(spec: Spec, raw: str) -> Any:
    """Turn typed text into the value, or raise ValueError with a Dutch message."""
    text = raw.strip()
    if spec.kind == "bool":
        if text.lower() in ("true", "1", "yes", "aan"):
            return True
        if text.lower() in ("false", "0", "no", "uit"):
            return False
        raise ValueError("kies aan of uit")
    if spec.kind == "choice":
        if text.lower() in spec.choices:
            return text.lower()
        raise ValueError("kies een van: " + ", ".join(spec.choices))

    try:
        value: float = int(text) if spec.kind == "int" else float(text.replace(",", "."))
    except ValueError:
        raise ValueError("moet een geheel getal zijn" if spec.kind == "int" else "moet een getal zijn") from None
    if spec.minimum is not None and value < spec.minimum or spec.maximum is not None and value > spec.maximum:
        raise ValueError(f"moet tussen {spec.minimum:g} en {spec.maximum:g} liggen")
    return value


def to_text(value: Any) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


# --- storage -------------------------------------------------------------


def _rows(db: Session) -> dict[str, models.RuntimeSetting]:
    return {row.key: row for row in db.query(models.RuntimeSetting).all()}


def _parse_or_none(spec: Spec, raw: str | None) -> Any:
    if raw is None:
        return None
    try:
        return parse(spec, raw)
    except ValueError:
        return None


def env_value(spec: Spec, row: models.RuntimeSetting | None) -> Any:
    """The value .env gives: read directly for this service's own settings,
    as last reported by the scheduler for the scheduler's; None until known."""
    if spec.owner == "scoring":
        return getattr(settings, spec.attr)  # type: ignore[arg-type]
    return _parse_or_none(spec, row.env_value if row else None)


def get(db: Session, key: str) -> Any:
    """Effective value for a setting of this service: the override if there is
    one, else what .env gives."""
    spec = _BY_KEY[key]
    row = db.get(models.RuntimeSetting, key)
    override = _parse_or_none(spec, row.override if row else None)
    return override if override is not None else env_value(spec, row)


@dataclass(frozen=True)
class Row:
    spec: Spec
    override: Any  # None = follows .env
    env: Any  # None = not known yet
    effective: Any


def describe_all(db: Session) -> list[Row]:
    rows = _rows(db)
    result = []
    for spec in SPECS:
        row = rows.get(spec.key)
        override = _parse_or_none(spec, row.override if row else None)
        env = env_value(spec, row)
        effective = override if override is not None else (env if env is not None else spec.default)
        result.append(Row(spec, override, env, effective))
    return result


def overrides_for_scheduler(db: Session) -> dict[str, Any]:
    """Typed overrides of the scheduler's settings, for GET /settings/runtime."""
    rows = _rows(db)
    overrides = {}
    for spec in SPECS:
        if spec.owner != "scheduler":
            continue
        value = _parse_or_none(spec, rows[spec.key].override if spec.key in rows else None)
        if value is not None:
            overrides[spec.key] = value
    return overrides


def store_env_baseline(db: Session, values: dict[str, str]) -> int:
    """Record the .env values the scheduler reports. Only the scheduler's
    own registry keys are taken; anything else is ignored."""
    stored = 0
    rows = _rows(db)
    for key, raw in values.items():
        spec = _BY_KEY.get(key)
        if spec is None or spec.owner != "scheduler" or _parse_or_none(spec, raw) is None:
            continue
        row = rows.get(key) or models.RuntimeSetting(key=key)
        row.env_value = raw
        db.add(row)
        stored += 1
    db.commit()
    return stored


def save_overrides(db: Session, submitted: dict[str, str]) -> tuple[list[str], dict[str, str]]:
    """Apply the GUI form: a blank value clears the override (follow .env), a
    filled one sets it. All-or-nothing: on any error nothing is written.

    Returns (keys whose override changed, {key: error message}).
    """
    errors: dict[str, str] = {}
    new_values: dict[str, Any] = {}  # key -> typed value, None = clear
    for spec in SPECS:
        raw = submitted.get(spec.key)
        if raw is None:
            continue  # field not in the form: leave as is
        if not raw.strip():
            new_values[spec.key] = None
            continue
        try:
            new_values[spec.key] = parse(spec, raw)
        except ValueError as exc:
            errors[spec.key] = str(exc)

    # A "3 of 5" style rule that can never be met is a mistake, not a setting.
    def effective_after(key: str) -> Any:
        if key in new_values and new_values[key] is not None:
            return new_values[key]
        if key in new_values:  # cleared -> .env
            return env_value(_BY_KEY[key], db.get(models.RuntimeSetting, key))
        return get(db, key)

    for count_key, window_key in (
        ("SOURCE_ACTIVATION_MIN_HIGH_SCORE", "SOURCE_ACTIVATION_WINDOW"),
        ("SOURCE_DEACTIVATION_MIN_NEGATIVE", "SOURCE_DEACTIVATION_WINDOW"),
    ):
        if count_key in errors or window_key in errors:
            continue
        count, window = effective_after(count_key), effective_after(window_key)
        if count is not None and window is not None and count > window:
            errors[count_key] = f"mag niet groter zijn dan '{_BY_KEY[window_key].label}' ({window})"

    if errors:
        return [], errors

    rows = _rows(db)
    changed: list[str] = []
    for key, value in new_values.items():
        row = rows.get(key)
        old = _parse_or_none(_BY_KEY[key], row.override if row else None)
        if value == old:
            continue
        if row is None:
            row = models.RuntimeSetting(key=key)
            db.add(row)
        row.override = None if value is None else to_text(value)
        row.updated_at = datetime.now(timezone.utc)
        changed.append(key)
    db.commit()
    return changed, {}
