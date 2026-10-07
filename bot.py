"""
Bot Discord — Achat de numéros Twitter/X USA via HeroSMS
Style identique au bot Instagram (Kyce - Insta)
Auteur : Mael Marca
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("twitter-bot")

# ── ENV ──
def env(key, default=""):
    return os.getenv(key, default).strip().strip('"').strip("'")

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
DAILY_QUOTA        = int(env("DAILY_QUOTA", "0"))
DAILY_ATTEMPTS_MAX = int(env("DAILY_ATTEMPTS_MAX", "0"))

# ── COMPTEURS ──
daily_success: dict[int, int] = defaultdict(int)
daily_attempts: dict[int, int] = defaultdict(int)
last_reset: str = dt.date.today().isoformat()

def _reset_if_new_day():
    global last_reset
    today = dt.date.today().isoformat()
    if today != last_reset:
        daily_success.clear()
        daily_attempts.clear()
        last_reset = today

_user_locks: dict[int, float] = {}
_cancel_events: dict[str, asyncio.Event] = {}

# ── BOT ──
intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)
http_session: aiohttp.ClientSession | None = None

# ── HEROSMS API ──
async def hero_call(params: dict) -> str:
    params["api_key"] = HERO_API_KEY
    async with http_session.get(HERO_BASE, params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
        text = await r.text()
        log.info("HeroSMS %s → %s", params.get("action"), text[:200])
        return text.strip()

async def hero_get_balance() -> str:
    return await hero_call({"action": "getBalance"})

async def hero_buy_number():
    text = await hero_call({
        "action": "getNumberV2",
        "service": SERVICE,
        "country": HERO_COUNTRY,
        "maxPrice": MAX_PRICE,
    })
    if "ACCESS_NUMBER" in text:
        parts = text.split(":")
        if len(parts) >= 3:
            return parts[1], parts[2], parts[3] if len(parts) >= 4 else "?"
    if "{" in text:
        import json
        try:
            data = json.loads(text)
            a, p, c = str(data.get("activationId","")), str(data.get("phoneNumber","")), str(data.get("activationCost","?"))
            if a and p:
                return a, p, c
        except Exception:
            pass
    return text

async def hero_arm(act_id):
    return await hero_call({"action": "setStatus", "id": act_id, "status": "1"})

async def hero_get_status(act_id):
    return await hero_call({"action": "getStatus", "id": act_id})

async def hero_cancel(act_id):
    return await hero_call({"action": "setStatus", "id": act_id, "status": "8"})

async def hero_complete(act_id):
    return await hero_call({"action": "setStatus", "id": act_id, "status": "6"})

async def hero_resend(act_id):
    return await hero_call({"action": "setStatus", "id": act_id, "status": "3"})

async def hero_get_prices():
    return await hero_call({"action": "getPrices", "service": SERVICE, "country": HERO_COUNTRY})

async def hero_get_services():
    return await hero_call({"action": "getServicesList"})

# ── LOGS STRUCTURÉS (style Instagram) ──
async def log_numero_pris(user, act_id, phone, cost):
    try:
        ch = bot.get_channel(LOG_CHANNEL_ID)
        if not ch: return
        _reset_if_new_day()
        v = daily_success.get(user.id, 0)
        t = daily_attempts.get(user.id, 0)
        now = dt.datetime.now().strftime("Aujourd'hui à %H:%M")
        embed = discord.Embed(title="📋 Numero pris", color=0x1DA1F2)
        embed.add_field(name="VA", value=f"{user.mention}\n{user.display_name}", inline=True)
        embed.add_field(name="Type", value="Numero USA —\nTwitter", inline=True)
        embed.add_field(name="Valides (Numero USA —\nTwitter)", value=f"{v}\n({t} pris)", inline=True)
        embed.add_field(name="Numero", value=phone, inline=True)
        embed.add_field(name="Activation", value=act_id, inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)
        embed.add_field(name="Info", value=f"Prix payé : **${cost}**\nID Discord : {user.id} • {now}", inline=False)
        await ch.send(embed=embed)
    except Exception as e:
        log.error("log_numero_pris: %s", e)

async def log_code_recu(user, act_id, phone, code, cost):
    try:
        ch = bot.get_channel(LOG_CHANNEL_ID)
        if not ch: return
        _reset_if_new_day()
        v = daily_success.get(user.id, 0)
        t = daily_attempts.get(user.id, 0)
        now = dt.datetime.now().strftime("Aujourd'hui à %H:%M")
        embed = discord.Embed(title="✅ Code reçu", color=0x2ECC71)
        embed.add_field(name="VA", value=f"{user.mention}\n{user.display_name}", inline=True)
        embed.add_field(name="Type", value="Numero USA —\nTwitter", inline=True)
        embed.add_field(name="Valides (Numero USA —\nTwitter)", value=f"{v}\n({t} pris)", inline=True)
        embed.add_field(name="Numero", value=phone, inline=True)
        embed.add_field(name="Activation", value=act_id, inline=True)
        embed.add_field(name="Code", value=f"**{code}**", inline=True)
        embed.add_field(name="Info", value=f"Prix payé : **${cost}**\nID Discord : {user.id} • {now}", inline=False)
        await ch.send(embed=embed)
    except Exception as e:
        log.error("log_code_recu: %s", e)

async def log_pas_de_code(user, act_id, phone, cost, last_status=""):
    try:
        ch = bot.get_channel(LOG_CHANNEL_ID)
        if not ch: return
        _reset_if_new_day()
        v = daily_success.get(user.id, 0)
        t = daily_attempts.get(user.id, 0)
        now = dt.datetime.now().strftime("Aujourd'hui à %H:%M")
        embed = discord.Embed(title="📋 Aucun code (expire)", color=0xE74C3C)
        embed.add_field(name="VA", value=f"{user.mention}\n{user.display_name}", inline=True)
        embed.add_field(name="Type", value="Numero USA —\nTwitter", inline=True)
        embed.add_field(name="Valides (Numero USA —\nTwitter)", value=f"{v}\n({t} pris)", inline=True)
        embed.add_field(name="Numero", value=phone, inline=True)
        embed.add_field(name="Activation", value=act_id, inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)
        info = f"Ne compte pas dans le quota du VA.\nPrix payé : **${cost}**\nID Discord : {user.id} • {now}"
        if last_status:
            info += f"\nDernier statut HeroSMS : `{last_status}`"
        embed.add_field(name="Info", value=info, inline=False)
        await ch.send(embed=embed)
    except Exception as e:
        log.error("log_pas_de_code: %s", e)

async def log_annule_par_va(user, act_id, phone, cost):
    try:
        ch = bot.get_channel(LOG_CHANNEL_ID)
        if not ch: return
        _reset_if_new_day()
        v = daily_success.get(user.id, 0)
        t = daily_attempts.get(user.id, 0)
        now = dt.datetime.now().strftime("Aujourd'hui à %H:%M")
        embed = discord.Embed(title="📋 Annule par le VA", color=0xF39C12)
        embed.add_field(name="VA", value=f"{user.mention}\n{user.display_name}", inline=True)
        embed.add_field(name="Type", value="Numero USA —\nTwitter", inline=True)
        embed.add_field(name="Valides (Numero USA —\nTwitter)", value=f"{v}\n({t} pris)", inline=True)
        embed.add_field(name="Numero", value=phone, inline=True)
        embed.add_field(name="Activation", value=act_id, inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)
        embed.add_field(name="Info", value=f"Ne compte pas dans le quota du VA.\nID Discord : {user.id} • {now}", inline=False)
        await ch.send(embed=embed)
    except Exception as e:
        log.error("log_annule_par_va: %s", e)

async def log_simple(msg):
    try:
        ch = bot.get_channel(LOG_CHANNEL_ID)
        if ch: await ch.send(msg[:2000])
    except Exception as e:
        log.error("log_simple: %s", e)

# ── RETRY CANCEL EN ARRIÈRE-PLAN ──
async def _retry_cancel(act_id):
    for attempt in range(10):
        await asyncio.sleep(30)
        try:
            resp = await hero_cancel(act_id)
            if "EARLY_CANCEL_DENIED" not in resp:
                log.info("Annulation arrière-plan réussie: act=%s", act_id)
                await log_simple(f"🔄 Annulation arrière-plan #{act_id} réussie — {resp}")
                return
        except Exception as e:
            log.error("Retry cancel act=%s: %s", act_id, e)
    await log_simple(f"⚠️ Annulation #{act_id} échouée après 10 essais — remboursement auto 20min")

# ── BOUTONS APRÈS ACHAT (style Instagram) ──
class NumberActionsView(discord.ui.View):
    def __init__(self, phone, act_id, cost, user_id):
        super().__init__(timeout=300)
        self.phone = phone
        self.act_id = act_id
        self.cost = cost
        self.user_id = user_id

    @discord.ui.button(label="📋  Autre SMS", style=discord.ButtonStyle.grey)
    async def resend_sms(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Ce n'est pas ton numéro.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        resp = await hero_resend(self.act_id)
        if "ACCESS_RETRY_GET" in resp or "READY" in resp.upper():
            await interaction.followup.send("📨 Nouveau SMS demandé. Patiente…", ephemeral=True)
        else:
            await interaction.followup.send(f"⚠️ Réponse : `{resp}`", ephemeral=True)

    @discord.ui.button(label="❌  Annuler", style=discord.ButtonStyle.red)
    async def cancel_number(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Ce n'est pas ton numéro.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        # Arrêter le polling
        event = _cancel_events.get(self.act_id)
        if event:
            event.set()

        # Tenter annulation
        cancel_resp = await hero_cancel(self.act_id)
        if "EARLY_CANCEL_DENIED" in cancel_resp:
            asyncio.create_task(_retry_cancel(self.act_id))

        embed_cancelled = discord.Embed(
            title="🔹 Numero USA — Twitter",
            description=f"`{self.phone}` — annulé.\n\nActivation {self.act_id} — visible uniquement par toi.\n\n✅ **Numéro annulé. Tu peux en reprendre un autre.**",
            color=0x95A5A6,
        )
        await interaction.followup.send(embed=embed_cancelled, ephemeral=True)
        await log_annule_par_va(interaction.user, self.act_id, self.phone, self.cost)


# ── PANNEAU PERSISTANT ──
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

        # Rôle
        if ALLOWED_ROLE_ID:
            if ALLOWED_ROLE_ID not in [r.id for r in user.roles]:
                await interaction.response.send_message("❌ Tu n'as pas le rôle nécessaire.", ephemeral=True)
                return

        # Cooldown
        now = asyncio.get_event_loop().time()
        if now - _user_locks.get(uid, 0) < COOLDOWN_SECONDS:
            await interaction.response.send_message(f"⏳ Attends {COOLDOWN_SECONDS}s.", ephemeral=True)
            return
        _user_locks[uid] = now

        # Quotas
        _reset_if_new_day()
        if DAILY_QUOTA and daily_success[uid] >= DAILY_QUOTA:
            await interaction.response.send_message(f"🚫 Limite de **{DAILY_QUOTA}** codes atteinte.", ephemeral=True)
            return
        if DAILY_ATTEMPTS_MAX and daily_attempts[uid] >= DAILY_ATTEMPTS_MAX:
            await interaction.response.send_message(f"🚫 Limite de **{DAILY_ATTEMPTS_MAX}** tentatives atteinte.", ephemeral=True)
            return

        daily_attempts[uid] += 1
        await interaction.response.defer(ephemeral=True)

        # Achat
        result = await hero_buy_number()
        if isinstance(result, str):
            err = result
            if "NO_BALANCE" in err: err = "💰 Plus de solde HeroSMS. Préviens Mael."
            elif "NO_NUMBERS" in err: err = "📵 Aucun numéro dispo. Réessaie dans quelques minutes."
            elif "BAD_KEY" in err: err = "🔑 Clé API invalide. Préviens Mael."
            elif "BANNED" in err: err = "⛔ Service banni. Préviens Mael."
            else: err = f"❌ Erreur : `{err}`"
            await interaction.followup.send(err, ephemeral=True)
            await log_simple(f"❌ {user.display_name} — échec achat Twitter : {result}")
            return

        act_id, phone, cost = result
        phone_raw = phone if not phone.startswith("+") else phone[1:]
        log.info("Acheté: act=%s phone=%s cost=%s user=%s", act_id, phone, cost, user)

        # Armer
        await hero_arm(act_id)

        # Event annulation
        cancel_event = asyncio.Event()
        _cancel_events[act_id] = cancel_event

        # Afficher le numéro (style Instagram)
        embed_num = discord.Embed(
            title="🔹 Numero USA — Twitter",
            description=(
                f"# `{phone_raw}`\n\n"
                f"🇺🇸 United States · 🏆 · 3 min · {user.display_name}\n\n"
                f"⚠️ **PAS DE CODE ? ANNULE ET PRENDS-EN UN AUTRE**\n\n"
                f"Activation {act_id} — visible uniquement par toi."
            ),
            color=0x1DA1F2,
        )
        actions_view = NumberActionsView(phone_raw, act_id, cost, uid)
        await interaction.followup.send(embed=embed_num, view=actions_view, ephemeral=True)

        # DM
        try:
            await user.send(f"📱 **Twitter** — Ton numéro : `{phone_raw}`\nActivation #{act_id}")
        except discord.Forbidden:
            pass

        await log_numero_pris(user, act_id, phone_raw, cost)

        # Polling
        code = None
        last_status = ""
        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < POLL_TIMEOUT:
            if cancel_event.is_set():
                last_status = "CANCELLED_BY_VA"
                break
            await asyncio.sleep(5)
            status = await hero_get_status(act_id)
            last_status = status
            if status.startswith("STATUS_OK"):
                code = status.split(":", 1)[1] if ":" in status else status
                break
            if "STATUS_CANCEL" in status:
                break

        _cancel_events.pop(act_id, None)

        if code:
            await hero_complete(act_id)
            daily_success[uid] += 1

            embed_code = discord.Embed(
                title="✅ Code reçu — Twitter",
                description=(
                    f"# `{phone_raw}`\n\n"
                    f"# 🔑  {code}\n\n"
                    f"Copie le code ci-dessus dans Twitter.\n"
                    f"Activation {act_id} — visible uniquement par toi."
                ),
                color=0x2ECC71,
            )
            await interaction.followup.send(embed=embed_code, ephemeral=True)

            try:
                await user.send(f"✅ **Code Twitter reçu !**\n📱 `{phone_raw}`\n🔑 Code : **{code}**")
            except discord.Forbidden:
                pass

            await log_code_recu(user, act_id, phone_raw, code, cost)

        elif not cancel_event.is_set():
            # Timeout
            cancel_resp = await hero_cancel(act_id)
            if "EARLY_CANCEL_DENIED" in cancel_resp:
                asyncio.create_task(_retry_cancel(act_id))

            embed_fail = discord.Embed(
                title="🔹 Numero USA — Twitter",
                description=(
                    f"# `{phone_raw}`\n\n"
                    f"⏱️ Pas de code reçu.\n\n"
                    f"⚠️ **PAS DE CODE ? PRENDS-EN UN AUTRE**\n\n"
                    f"Activation {act_id} — visible uniquement par toi."
                ),
                color=0xE74C3C,
            )
            await interaction.followup.send(embed=embed_fail, ephemeral=True)

            try:
                await user.send(f"⏱️ **Pas de code Twitter** — `{phone_raw}` — Reprends-en un autre.")
            except discord.Forbidden:
                pass

            await log_pas_de_code(user, act_id, phone_raw, cost, last_status)


# ── COMMANDES SLASH ──

@bot.tree.command(name="installer", description="Publie le panneau Twitter dans ce salon")
@app_commands.default_permissions(administrator=True)
async def cmd_installer(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📱  Numéro Twitter / X",
        description="Clique sur le bouton pour obtenir un numéro de téléphone américain.\nLe bot achètera le numéro et te donnera le code SMS.",
        color=0x1DA1F2,
    )
    await interaction.channel.send(embed=embed, view=TwitterPanel())
    await interaction.response.send_message("✅ Panneau publié.", ephemeral=True)

@bot.tree.command(name="stats", description="Numéros pris aujourd'hui par VA")
@app_commands.default_permissions(administrator=True)
async def cmd_stats(interaction: discord.Interaction):
    _reset_if_new_day()
    if not daily_success and not daily_attempts:
        await interaction.response.send_message("Aucune activité aujourd'hui.", ephemeral=True)
        return
    lines = []
    for uid in sorted(set(daily_success) | set(daily_attempts)):
        m = interaction.guild.get_member(uid)
        name = m.display_name if m else str(uid)
        lines.append(f"**{name}** — {daily_success.get(uid,0)} codes ✅ / {daily_attempts.get(uid,0)} tentatives")
    embed = discord.Embed(
        title=f"📊  Stats Twitter — {dt.date.today().strftime('%d/%m/%Y')}",
        description="\n".join(lines), color=0x1DA1F2,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="prix", description="Prix et stock HeroSMS pour Twitter USA")
@app_commands.default_permissions(administrator=True)
async def cmd_prix(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    balance = await hero_get_balance()
    prices_raw = await hero_get_prices()
    info = f"**Solde** : `{balance}`\n\n"
    try:
        import json
        data = json.loads(prices_raw)
        if HERO_COUNTRY in data and SERVICE in data[HERO_COUNTRY]:
            s = data[HERO_COUNTRY][SERVICE]
            info += f"**Service** : `{SERVICE}` (Twitter)\n**Pays** : USA (`{HERO_COUNTRY}`)\n"
            if isinstance(s, dict):
                for price, count in sorted(s.items()):
                    info += f"• ${price} — {count} dispo\n"
            else:
                info += f"`{s}`\n"
        else:
            info += f"```{prices_raw[:1000]}```"
    except Exception:
        info += f"```{prices_raw[:1000]}```"
    await interaction.followup.send(embed=discord.Embed(title="💰 Prix Twitter USA", description=info, color=0x1DA1F2), ephemeral=True)

@bot.tree.command(name="services", description="Liste des services HeroSMS")
@app_commands.default_permissions(administrator=True)
async def cmd_services(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    raw = await hero_get_services()
    try:
        import json
        data = json.loads(raw)
        lines = [f"`{c}` — {(i if isinstance(i,str) else i.get('name',c))}" for c,i in sorted(data.items())]
        text = "\n".join(lines[:50])
        if len(lines) > 50: text += f"\n… +{len(lines)-50}"
    except Exception:
        text = f"```{raw[:1800]}```"
    await interaction.followup.send(embed=discord.Embed(title="📋 Services HeroSMS", description=text[:4000], color=0x1DA1F2), ephemeral=True)

@bot.tree.command(name="verifier", description="Vérifie une activation HeroSMS")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(activation_id="ID de l'activation")
async def cmd_verifier(interaction: discord.Interaction, activation_id: str):
    await interaction.response.defer(ephemeral=True)
    status = await hero_get_status(activation_id.strip())
    await interaction.followup.send(embed=discord.Embed(title=f"🔎 Activation #{activation_id}", description=f"```{status}```", color=0x1DA1F2), ephemeral=True)

# ── EVENTS ──
@bot.event
async def on_ready():
    global http_session
    http_session = aiohttp.ClientSession()
    bot.add_view(TwitterPanel())
    if GUILD_ID:
        g = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=g)
        await bot.tree.sync(guild=g)
    else:
        await bot.tree.sync()
    log.info("Bot connecté : %s (ID %s)", bot.user, bot.user.id)

@bot.event
async def on_error(event, *args, **kwargs):
    log.exception("Erreur dans %s", event)

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
