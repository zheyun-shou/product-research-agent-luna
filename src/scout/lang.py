"""The fixed texts that Python shows to the shopper, in English, Swedish and Chinese. The chat model writes its own
replies in the language of the shopper; these texts follow the same language.

The language of a turn comes from the last message of the shopper. A short message without a clear sign (such as
"ok" or "2000 kr") keeps the language of the turn before. The tool reports for the chat model stay in English."""
import re

LANGS = ("en", "sv", "zh")
CJK = re.compile(r"[㐀-鿿豈-﫿]")
WORD = re.compile(r"[a-zåäöéü]+")
# Common short words that are only Swedish or only English. Not words that the other language or a shop also
# uses, such as "kr", "min", "men", "de", "om" (Swedish) or "i" (English).
SV_WORDS = frozenset(
    "och är jag en ett att det som för med inte till på av har vill kan ska eller vad hur var mig mitt mina den du "
    "ni vi så också bara mer mindre än utan efter innan ingen inga något några bra billig billigt tyst tystare "
    "köpa behöver hitta letar söker gärna tack hej".split())
EN_WORDS = frozenset(
    "and is a an the to it that for with not of have want can should or but if what how was me my mine you we so "
    "also only more less than without after before no some good cheap quiet buy need find looking please thanks hi "
    "which this these those".split())


def detect(text: str) -> str | None:
    """The language of a message of the shopper, or None when it shows no clear sign."""
    if CJK.search(text):
        return "zh"
    words = WORD.findall(text.lower())
    sv = sum(w in SV_WORDS for w in words) + (2 if re.search(r"[åäö]", text.lower()) else 0)
    en = sum(w in EN_WORDS for w in words)
    if sv > en:
        return "sv"
    if en > sv:
        return "en"
    return None


def turn_lang(messages: list[dict], before: str | None = None) -> str:
    """The language of the turn: the last message of the shopper, else the language of the turn before."""
    last = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    return detect(last) or before or "en"


TEXTS: dict[str, dict[str, str]] = {
    # Python's messages in the conversation
    "llm_unavailable": {
        "en": "I cannot work on this now. {exc}",
        "sv": "Jag kan inte arbeta med det här nu. {exc}",
        "zh": "我现在无法处理这个请求。{exc}"},
    "llm_output": {
        "en": "The model did not give a usable reply ({exc}). You can say it in other words.",
        "sv": "Modellen gav inget användbart svar ({exc}). Du kan säga det med andra ord.",
        "zh": "模型没有给出可用的回复（{exc}）。你可以换个说法再试。"},
    "turn_limits": {
        "en": "I could not finish this within the limits of one turn. You can ask me to go on.",
        "sv": "Jag hann inte bli klar inom gränserna för en tur. Du kan be mig fortsätta.",
        "zh": "这一轮的额度用完了，我还没做完。你可以让我继续。"},
    "gate_gave_up": {
        "en": "I could not finish the research for these picks, so I changed nothing. You can ask again.",
        "sv": "Jag kunde inte göra klart undersökningen av de här förslagen, så jag ändrade ingenting. "
              "Du kan fråga igen.",
        "zh": "这些推荐的调研没能完成，所以我什么都没改。你可以再问一次。"},
    "went_wrong": {
        "en": "Something went wrong ({exc}). You can try again.",
        "sv": "Något gick fel ({exc}). Du kan försöka igen.",
        "zh": "出了点问题（{exc}）。你可以再试一次。"},
    "picks_not_loaded": {
        "en": "The picks could not be loaded ({exc}).",
        "sv": "Förslagen kunde inte laddas ({exc}).",
        "zh": "推荐列表没能加载（{exc}）。"},
    "removed": {
        "en": "Removed: {title}. It stays out for the rest of this conversation.",
        "sv": "Borttagen: {title}. Den är borta under resten av samtalet.",
        "zh": "已删除：{title}。这次对话里不会再出现。"},
    # The lines of the picks
    "price_unknown": {"en": "price unknown", "sv": "pris okänt", "zh": "价格未知"},
    "ratings": {"en": "{rating} ({count} ratings)", "sv": "{rating} ({count} betyg)", "zh": "{rating}（{count} 个评分）"},
    "no_ratings": {"en": "no ratings", "sv": "inga betyg", "zh": "没有评分"},
    # The status lines of a tool
    "say_search_shop": {
        "en": "Searching amazon.se for '{words}', result page {page}. This can take a few minutes.",
        "sv": "Söker på amazon.se efter '{words}', resultatsida {page}. Det kan ta några minuter.",
        "zh": "正在 amazon.se 搜索“{words}”，第 {page} 页结果。可能要几分钟。"},
    "say_search_web": {
        "en": "Searching the web for '{question}'.",
        "sv": "Söker på webben efter '{question}'.",
        "zh": "正在网上搜索“{question}”。"},
    "say_research": {
        "en": "Researching {n} product(s) on the web at the same time. This can take a few minutes.",
        "sv": "Undersöker {n} produkt(er) på webben samtidigt. Det kan ta några minuter.",
        "zh": "正在同时上网调研 {n} 个商品。可能要几分钟。"},
    "say_candidates": {
        "en": "{n} researcher(s) check the candidates at the same time. This can take a few minutes.",
        "sv": "{n} forskare kontrollerar kandidaterna samtidigt. Det kan ta några minuter.",
        "zh": "{n} 个调研员正在同时调研候选商品。可能要几分钟。"},
    "say_read_page": {"en": "Reading {url}", "sv": "Läser {url}", "zh": "正在读取 {url}"},
    "say_reviews": {
        "en": "Reading the positive and the critical shop reviews of the picks on amazon.se. This needs the login "
              "in the Luna Chrome window.",
        "sv": "Läser de positiva och de kritiska recensionerna av förslagen på amazon.se. Det kräver inloggningen "
              "i Chrome-fönstret för Luna.",
        "zh": "正在读取推荐商品在 amazon.se 上的好评和差评。需要在 Luna 的 Chrome 窗口里登录。"},
    # The steps of a turn on the web page
    "step_plan_first": {"en": "Read the message and plan the work", "sv": "Läs meddelandet och planera arbetet",
                        "zh": "阅读消息，规划工作"},
    "step_plan": {"en": "Plan the next step", "sv": "Planera nästa steg", "zh": "规划下一步"},
    "step_check_picks": {
        "en": "Check the picks: the price and stock now, the reviews, the web tests",
        "sv": "Kontrollera förslagen: pris och lager nu, recensionerna, testerna på webben",
        "zh": "检查推荐：当前价格和库存、评价、网上测评"},
    "step_check_reply": {"en": "Check the reply", "sv": "Kontrollera svaret", "zh": "检查回复"},
    "step_checks_pass": {"en": "All checks pass", "sv": "Alla kontroller godkända", "zh": "全部检查通过"},
    "step_problems": {
        "en": "{n} problem(s) sent back to the model",
        "sv": "{n} problem skickade tillbaka till modellen",
        "zh": "{n} 个问题退回给模型"},
    "step_needs": {"en": "Record the needs", "sv": "Spara behoven", "zh": "记录需求"},
    "step_search_shop": {"en": "Search amazon.se for '{words}'", "sv": "Sök på amazon.se efter '{words}'",
                         "zh": "在 amazon.se 搜索“{words}”"},
    "step_page": {"en": ", result page {page}", "sv": ", resultatsida {page}", "zh": "，第 {page} 页"},
    "step_search_web": {"en": "Search the web for '{question}'", "sv": "Sök på webben efter '{question}'",
                        "zh": "在网上搜索“{question}”"},
    "step_research": {"en": "Research {n} product(s) on the web", "sv": "Undersök {n} produkt(er) på webben",
                      "zh": "上网调研 {n} 个商品"},
    "step_candidates": {"en": "Research {n} candidate(s), one researcher each",
                        "sv": "Undersök {n} kandidat(er), en forskare för varje",
                        "zh": "调研 {n} 个候选，每个一个调研员"},
    "step_read_page": {"en": "Read a shop page on {host}", "sv": "Läs en butikssida på {host}",
                       "zh": "读取 {host} 上的商品页"},
    "step_same_product": {"en": "Join two listings of one product (ids {id} and {same})",
                          "sv": "Slå ihop två annonser för en produkt (id {id} och {same})",
                          "zh": "把同一商品的两个链接合并（id {id} 和 {same}）"},
    "step_find_products": {"en": "Find the stored products that meet the needs",
                           "sv": "Hitta sparade produkter som uppfyller behoven", "zh": "查找已存的符合需求的商品"},
    "step_read_product": {"en": "Read the stored pages of {n} product(s)", "sv": "Läs sparade sidor för {n} produkt(er)",
                          "zh": "读取 {n} 个商品的已存页面"},
    "step_search_text": {"en": "Search the stored text for '{question}'", "sv": "Sök i sparad text efter '{question}'",
                         "zh": "在已存文本里搜索“{question}”"},
    "step_similar": {"en": "Find products near {near}", "sv": "Hitta produkter nära {near}", "zh": "查找接近 {near} 的商品"},
    "step_similar_product": {"en": "product {id}", "sv": "produkt {id}", "zh": "商品 {id}"},
    "step_and": {"en": " and ", "sv": " och ", "zh": "和"},
    "step_nothing": {"en": "nothing", "sv": "ingenting", "zh": "无"},
    "step_images": {"en": "Look at the images of product {id}", "sv": "Titta på bilderna av produkt {id}",
                    "zh": "查看商品 {id} 的图片"},
    "step_missing": {"en": "Look for missing values ({names})", "sv": "Leta efter saknade värden ({names})",
                     "zh": "查找缺失的数值（{names}）"},
    "step_read_reviews": {"en": "Read the amazon.se reviews of {n} product(s)",
                          "sv": "Läs recensionerna på amazon.se för {n} produkt(er)",
                          "zh": "读取 {n} 个商品在 amazon.se 上的评价"},
    "step_summarize": {"en": "Sum up the reviews of {n} product(s)", "sv": "Sammanfatta recensionerna för {n} produkt(er)",
                       "zh": "总结 {n} 个商品的评价"},
    "step_remove": {"en": "Remove {n} product(s)", "sv": "Ta bort {n} produkt(er)", "zh": "移除 {n} 个商品"},
    "step_restore": {"en": "Bring back {n} product(s)", "sv": "Ta tillbaka {n} produkt(er)", "zh": "恢复 {n} 个商品"},
    "step_remember_like": {"en": "Remember a like", "sv": "Kom ihåg något du gillar", "zh": "记住一条喜好"},
    "step_remember_dislike": {"en": "Remember a dislike", "sv": "Kom ihåg något du inte gillar",
                              "zh": "记住一条不喜欢的"},
    "step_remember_fact": {"en": "Remember a fact", "sv": "Kom ihåg ett faktum", "zh": "记住一条事实"},
    "step_profile": {"en": "Change the profile of the category", "sv": "Ändra profilen för kategorin",
                     "zh": "修改这个品类的配置"},
    "step_forget": {"en": "Forget {n} memory(s)", "sv": "Glöm {n} minne(n)", "zh": "删除 {n} 条记忆"},
    "step_think": {"en": "Think it over", "sv": "Tänk igenom", "zh": "思考一下"},
}
# Plural forms where the count matters
PLURAL = {"product(s)": ("product", "products"), "problem(s)": ("problem", "problems"),
          "memory(s)": ("memory", "memories"), "produkt(er)": ("produkt", "produkter"),
          "minne(n)": ("minne", "minnen"), "researcher(s)": ("researcher", "researchers"),
          "candidate(s)": ("candidate", "candidates"), "kandidat(er)": ("kandidat", "kandidater")}


def t(key: str, lang: str | None, **values) -> str:
    """The text of key in lang (English when lang has no text), with the values filled in."""
    texts = TEXTS[key]
    text = texts.get(lang or "en") or texts["en"]
    if "n" in values:
        for form, (one, more) in PLURAL.items():
            text = text.replace(form, one if values["n"] == 1 else more)
    return text.format(**values)
