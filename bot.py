"""
Bot Discord — Achat de numéros Twitter/X USA via HeroSMS
Auteur : Mael Marca
Stack  : Python 3.11+, discord.py 2.x, aiohttp
Déploiement : Railway (Procfile: worker: python bot.py)
"""

import os
import asyncio
import logging
import datetime as dt
from collections import defaultdict

import aiohttp
import discord
from discord.ext import commands
from discord import app_commands

# ──────────────────────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("twitter-bot")

# ──────────────────────────────────────────────────────────────
# ENV — Railway met parfois des guillemets autour des valeurs
# ──────────────────────────────────────────────────────────────
def env(key: str, default: str = "") -> str:
    val = os.getenv(key, default).strip().strip('"').strip("'")
    return val

DISCORD_TOKEN      = env("DISCORD_TOKEN")
GUILD_ID           = int(env("DISCORD_GUILD_ID", "0"))
CHANNEL_ID         = int(env("CHANNEL_TWITTER_ID", "0"))
LOG_CHANNEL_ID     = int(env("LOG_CHANNEL_ID", "0"))
ALLOWED_ROLE_ID    = int(env("ALLOWED_ROLE_ID", "0"))

HERO_API_KEY       = env("HERO_API_KEY")
HERO_BASE          = "https://hero-sms.com/stubs/handler_api.php"
HERO_COUNTRY       = env("HERO_COUNTRY", "187")
SERVICE            = env("SERVICE_TWITTER", "tw")
MAX_PRICE          = env("MAX_PRICE_TWITTER", "0.25")

POLL_TIMEOUT       = int(env("POLL_TIMEOUT", "180"))
COOLDOWN_SECONDS   = int(env("COOLDOWN_SECONDS", "5"))
DAILY_QUOTA        = int(env("DAILY_QUOTA", "0"))          # 0 = illimité
DAILY_ATTEMPTS_MAX = int(env("DAILY_ATTEMPTS_MAX", "0"))   # 0 = illimité

# ──────────────────────────────────────────────────────────────
# COMPTEURS QUOTIDIENS
# ──────────────────────────────────────────────────────────────
daily_success: dict[int, int] = defaultdict(int)   # user_id → codes reçus
daily_attempts: dict[int, int] = defaultdict(int)  # user_id → tentatives
last_reset: str = dt.date.today().isoformat()

def _reset_if_new_day():
    global last_reset
    today = dt.date.today().isoformat()
    if today != last_reset:
        daily_success.clear()
        daily_attempts.clear()
        last_reset = today

# Anti-spam par utilisateur
_user_locks: dict[int, float] = {}

# ──────────────────────────────────────────────────────────────
# BOT DISCORD
# ──────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
http_session: aiohttp.ClientSession | None = None

# ──────────────────────────────────────────────────────────────
# APPELS HEROSMS
# ──────────────────────────────────────────────────────────────
async def hero_call(params: dict) -> str:
    """Appel GET vers l'API HeroSMS. Renvoie le texte brut de la réponse."""
    params["api_key"] = HERO_API_KEY
    async with http_session.get(HERO_BASE, params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
        text = await r.text()
        log.info("HeroSMS %s → %s", params.get("action"), text[:200])
        return text.strip()


async def hero_get_balance() -> str:
    return await hero_call({"action": "getBalance"})


async def hero_buy_number() -> tuple[str, str, str] | str:
    """Achète un numéro Twitter/X USA.
    Retourne (activation_id, phone_number, cost) ou un message d'erreur."""
    text = await hero_call({
        "action": "getNumberV2",
        "service": SERVICE,
        "country": HERO_COUNTRY,
        "maxPrice": MAX_PRICE,
    })
    # Réponse attendue : ACCESS_NUMBER:id:phone:cost  (ou variante getNumberV2)
    # getNumberV2 peut renvoyer du JSON ou le format classique
    if "ACCESS_NUMBER" in text:
        parts = text.split(":")
        # ACCESS_NUMBER:activationId:phoneNumber
        if len(parts) >= 3:
            act_id = parts[1]
            phone = parts[2]
            cost = parts[3] if len(parts) >= 4 else "?"
            return act_id, phone, cost
    # Peut-être format JSON de getNumberV2
    if "{" in text:
        import json
        try:
            data = json.loads(text)
            act_id = str(data.get("activationId", ""))
            phone = str(data.get("phoneNumber", ""))
            cost = str(data.get("activationCost", "?"))
            if act_id and phone:
                return act_id, phone, cost
        except json.JSONDecodeError:
            pass
    return text  # message d'erreur brut


async def hero_arm(act_id: str) -> str:
    """Arme l'activation (setStatus=1). Obligatoire pour recevoir le SMS."""
    return await hero_call({"action": "setStatus", "id": act_id, "status": "1"})


async def hero_get_status(act_id: str) -> str:
    return await hero_call({"action": "getStatus", "id": act_id})


async def hero_cancel(act_id: str) -> str:
    return await hero_call({"action": "setStatus", "id": act_id, "status": "8"})


async def hero_complete(act_id: str) -> str:
    return await hero_call({"action": "setStatus", "id": act_id, "status": "6"})


async def hero_get_prices() -> str:
    return await hero_call({
        "action": "getPrices",
        "service": SERVICE,
        "country": HERO_COUNTRY,
    })


async def hero_get_services() -> str:
    return await hero_call({"action": "getServicesList"})


# ──────────────────────────────────────────────────────────────
# LOG INTERNE DISCORD
# ──────────────────────────────────────────────────────────────
async def log_to_channel(msg: str):
    try:
        ch = bot.get_channel(LOG_CHANNEL_ID)
        if ch:
            await ch.send(msg[:2000])
    except Exception as e:
        log.error("log_to_channel: %s", e)

# ──────────────────────────────────────────────────────────────
# VUE PERSISTANTE — BOUTON « PRENDRE UN NUMÉRO »
# ──────────────────────────────────────────────────────────────
class TwitterPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="📱  Prendre un numéro Twitter",
        style=discord.ButtonStyle.blurple,
        custom_id="twitter_take_number",
    )
    async def take_number(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        uid = user.id

        # ── Vérification du rôle ──
        if ALLOWED_ROLE_ID:
            role_ids = [r.id for r in user.roles]
            if ALLOWED_ROLE_ID not in role_ids:
                await interaction.response.send_message(
                    "❌ Tu n'as pas le rôle nécessaire.", ephemeral=True
                )
                return

        # ── Cooldown ──
        now = asyncio.get_event_loop().time()
        last = _user_locks.get(uid, 0)
        if now - last < COOLDOWN_SECONDS:
            await interaction.response.send_message(
                f"⏳ Attends {COOLDOWN_SECONDS}s entre deux demandes.", ephemeral=True
            )
            return
        _user_locks[uid] = now

        # ── Quotas ──
        _reset_if_new_day()

        if DAILY_QUOTA and daily_success[uid] >= DAILY_QUOTA:
            await interaction.response.send_message(
                f"🚫 Tu as atteint ta limite de **{DAILY_QUOTA}** codes valides aujourd'hui.",
                ephemeral=True,
            )
            return

        if DAILY_ATTEMPTS_MAX and daily_attempts[uid] >= DAILY_ATTEMPTS_MAX:
            await interaction.response.send_message(
                f"🚫 Tu as atteint ta limite de **{DAILY_ATTEMPTS_MAX}** tentatives aujourd'hui.",
                ephemeral=True,
            )
            return

        daily_attempts[uid] += 1

        # ── Defer immédiatement (< 3 s) ──
        await interaction.response.defer(ephemeral=True)

        # ── Achat ──
        result = await hero_buy_number()
        if isinstance(result, str):
            error_msg = result
            if "NO_BALANCE" in error_msg:
                error_msg = "💰 Plus de solde sur HeroSMS. Préviens Mael."
            elif "NO_NUMBERS" in error_msg:
                error_msg = "📵 Aucun numéro USA disponible pour Twitter en ce moment. Réessaie dans quelques minutes."
            elif "BAD_KEY" in error_msg:
                error_msg = "🔑 Clé API invalide. Préviens Mael."
            elif "BANNED" in error_msg:
                error_msg = "⛔ Service banni par HeroSMS. Préviens Mael."
            else:
                error_msg = f"❌ Erreur HeroSMS : `{error_msg}`"
            await interaction.followup.send(error_msg, ephemeral=True)
            await log_to_channel(f"❌ {user.display_name} — échec achat Twitter : {result}")
            return

        act_id, phone, cost = result
        log.info("Numéro acheté: act=%s phone=%s cost=%s user=%s", act_id, phone, cost, user)

        # ── Armer l'activation ──
        arm_resp = await hero_arm(act_id)
        if "ACCESS_READY" not in arm_resp and "READY" not in arm_resp.upper():
            log.warning("Armement inattendu: %s (act=%s)", arm_resp, act_id)

        # ── Afficher le numéro ──
        phone_display = phone if phone.startswith("+") else f"+{phone}"
        embed_num = discord.Embed(
            title="📱  TWITTER / X",
            description=f"# {phone_display}\n\nEntre ce numéro sur Twitter.\nJ'attends le code SMS…",
            color=0x1DA1F2,
        )
        embed_num.set_footer(text=f"Activation #{act_id} • Coût ${cost}")
        await interaction.followup.send(embed=embed_num, ephemeral=True)

        # ── DM avec le numéro ──
        try:
            await user.send(
                f"📱 **Twitter** — Ton numéro : **{phone_display}**\n"
                f"Activation `#{act_id}` • Coût `${cost}`"
            )
        except discord.Forbidden:
            pass

        await log_to_channel(
            f"📱 {user.display_name} (`{uid}`) — Twitter #{act_id} — "
            f"{phone_display} — ${cost}"
        )

        # ── Polling du code ──
        code = None
        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < POLL_TIMEOUT:
            await asyncio.sleep(5)
            status = await hero_get_status(act_id)
            if status.startswith("STATUS_OK"):
                code = status.split(":", 1)[1] if ":" in status else status
                break
            if "STATUS_CANCEL" in status:
                break

        # ── Résultat ──
        if code:
            # Marquer comme terminé
            await hero_complete(act_id)
            daily_success[uid] += 1

            embed_code = discord.Embed(
                title="✅  CODE REÇU",
                description=(
                    f"# 📱  {phone_display}\n"
                    f"# 🔑  {code}\n\n"
                    f"**Copie le code ci-dessus dans Twitter.**"
                ),
                color=0x2ECC71,
            )
            await interaction.followup.send(embed=embed_code, ephemeral=True)

            # DM avec le code
            try:
                await user.send(
                    f"✅ **Code Twitter reçu !**\n\n"
                    f"📱 Numéro : **{phone_display}**\n"
                    f"🔑 Code : **{code}**"
                )
            except discord.Forbidden:
                pass

            await log_to_channel(
                f"✅ {user.display_name} — Twitter #{act_id} — "
                f"{phone_display} — Code `{code}`"
            )
        else:
            # Tenter d'annuler (peut échouer si EARLY_CANCEL_DENIED)
            cancel_resp = await hero_cancel(act_id)
            cancelled = "ACCESS_CANCEL" in cancel_resp or "CANCEL" in cancel_resp.upper()
            cancel_note = ""
            if "EARLY_CANCEL_DENIED" in cancel_resp:
                cancel_note = "\n_Annulation refusée par HeroSMS — le numéro sera remboursé automatiquement après 20 min._"
            elif not cancelled:
                cancel_note = f"\n_Réponse annulation : `{cancel_resp}`_"

            embed_fail = discord.Embed(
                title="⏱️  PAS DE CODE",
                description=(
                    f"# 📱  {phone_display}\n\n"
                    f"**PAS DE CODE ? ANNULE ET PRENDS-EN UN AUTRE.**"
                    f"{cancel_note}"
                ),
                color=0xE74C3C,
            )
            await interaction.followup.send(embed=embed_fail, ephemeral=True)

            try:
                await user.send(
                    f"⏱️ **Pas de code Twitter**\n"
                    f"📱 Numéro : {phone_display}\n"
                    f"Reprends-en un autre."
                )
            except discord.Forbidden:
                pass

            await log_to_channel(
                f"⏱️ {user.display_name} — Twitter #{act_id} — "
                f"{phone_display} — Pas de code{cancel_note}"
            )


# ──────────────────────────────────────────────────────────────
# COMMANDES SLASH
# ──────────────────────────────────────────────────────────────

@bot.tree.command(name="installer", description="Publie le panneau Twitter dans ce salon")
@app_commands.default_permissions(administrator=True)
async def cmd_installer(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📱  Numéro Twitter / X",
        description=(
            "Clique sur le bouton pour obtenir un numéro de téléphone américain.\n"
            "Le bot achètera le numéro et te donnera le code SMS."
        ),
        color=0x1DA1F2,
    )
    view = TwitterPanel()
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("✅ Panneau publié.", ephemeral=True)


@bot.tree.command(name="stats", description="Numéros pris aujourd'hui par VA")
@app_commands.default_permissions(administrator=True)
async def cmd_stats(interaction: discord.Interaction):
    _reset_if_new_day()
    if not daily_success and not daily_attempts:
        await interaction.response.send_message("Aucune activité aujourd'hui.", ephemeral=True)
        return

    lines = []
    all_users = set(daily_success.keys()) | set(daily_attempts.keys())
    for uid in sorted(all_users):
        member = interaction.guild.get_member(uid)
        name = member.display_name if member else str(uid)
        s = daily_success.get(uid, 0)
        a = daily_attempts.get(uid, 0)
        lines.append(f"**{name}** — {s} codes ✅ / {a} tentatives")

    embed = discord.Embed(
        title=f"📊  Stats Twitter — {dt.date.today().strftime('%d/%m/%Y')}",
        description="\n".join(lines),
        color=0x1DA1F2,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="prix", description="Prix et stock HeroSMS pour Twitter USA")
@app_commands.default_permissions(administrator=True)
async def cmd_prix(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    balance = await hero_get_balance()
    prices_raw = await hero_get_prices()

    # Essayer de parser le JSON des prix
    info = f"**Solde** : `{balance}`\n\n"
    try:
        import json
        data = json.loads(prices_raw)
        # Format: { "187": { "tw": { "cost": X, "count": Y } } }
        if HERO_COUNTRY in data and SERVICE in data[HERO_COUNTRY]:
            s = data[HERO_COUNTRY][SERVICE]
            info += f"**Service** : `{SERVICE}` (Twitter)\n"
            info += f"**Pays** : USA (`{HERO_COUNTRY}`)\n"
            if isinstance(s, dict):
                for price, count in sorted(s.items()):
                    info += f"• ${price} — {count} numéros dispo\n"
            else:
                info += f"Données : `{s}`\n"
        else:
            info += f"Données brutes :\n```{prices_raw[:1000]}```"
    except Exception:
        info += f"Réponse :\n```{prices_raw[:1000]}```"

    embed = discord.Embed(
        title="💰  Prix Twitter USA — HeroSMS",
        description=info,
        color=0x1DA1F2,
    )
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="services", description="Liste des services HeroSMS")
@app_commands.default_permissions(administrator=True)
async def cmd_services(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    raw = await hero_get_services()

    try:
        import json
        data = json.loads(raw)
        lines = []
        for code, info in sorted(data.items()):
            name = info if isinstance(info, str) else info.get("name", info.get("serviceName", code))
            lines.append(f"`{code}` — {name}")
        text = "\n".join(lines[:50])
        if len(lines) > 50:
            text += f"\n… et {len(lines) - 50} autres"
    except Exception:
        text = f"```{raw[:1800]}```"

    embed = discord.Embed(
        title="📋  Services HeroSMS",
        description=text[:4000],
        color=0x1DA1F2,
    )
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="verifier", description="Vérifie l'état d'une activation HeroSMS")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(activation_id="L'identifiant de l'activation (ex: 123456)")
async def cmd_verifier(interaction: discord.Interaction, activation_id: str):
    await interaction.response.defer(ephemeral=True)
    status = await hero_get_status(activation_id.strip())
    embed = discord.Embed(
        title=f"🔎  Activation #{activation_id}",
        description=f"```{status}```",
        color=0x1DA1F2,
    )
    await interaction.followup.send(embed=embed, ephemeral=True)


# ──────────────────────────────────────────────────────────────
# ÉVÉNEMENTS
# ──────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    global http_session
    http_session = aiohttp.ClientSession()

    # Enregistrer la vue persistante
    bot.add_view(TwitterPanel())

    # Sync des commandes slash
    if GUILD_ID:
        guild = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
    else:
        await bot.tree.sync()

    log.info("Bot Twitter connecté : %s (ID %s)", bot.user, bot.user.id)
    log.info("Serveur: %s | Salon: %s | Log: %s", GUILD_ID, CHANNEL_ID, LOG_CHANNEL_ID)


@bot.event
async def on_error(event, *args, **kwargs):
    log.exception("Erreur non gérée dans %s", event)


async def main():
    if not DISCORD_TOKEN:
        log.error("DISCORD_TOKEN manquant !")
        return
    if not HERO_API_KEY:
        log.error("HERO_API_KEY manquant !")
        return

    async with bot:
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
