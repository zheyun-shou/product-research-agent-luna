// The fixed texts of the page in English, Swedish and Chinese. The language follows the shopper: the browser
// language first, then the language of each turn (the "lang" event of the server, scout/lang.py).
const TEXT = {
  en: {
    new_chat: "New chat", memory: "Memory", picks: "Picks",
    chats: "Chats", close_chats: "Close the chats", no_chats: "No chats yet.",
    chats_failed: "The chats could not be loaded.",
    hero_title: "What do you want to buy?",
    hero_text: "I search amazon.se, check each number against the product pages, and show my top picks.",
    problem_default: "amazon.se needs you",
    problem_help: "Log in or solve the check in the product-scout Chrome window, then click Done. I never type passwords.",
    problem_page: "The page", done: "Done", skip: "Skip",
    first_hint: "For example: a fridge with a freezer, under 180 cm, up to 6000 kr",
    next_hint: "Ask about the picks, or say what you want", wait_hint: "Wait for the reply",
    message: "Message", send: "Send", close_picks: "Close the picks",
    picks_hint: "Open a title to see it on amazon.se. Delete a pick to keep it out of this conversation.",
    removed: "Removed", bring_back: "To bring one back, ask in the chat.",
    memory_title: "What I remember", close_memory: "Close the memory",
    memory_hint: "I keep what you tell me that will still be true in a later chat, and the reasons when you remove a pick. Delete what is wrong.",
    doubtful: "The size on the page does not agree with the other sizes or the volume",
    no_image: "no image", price_unknown: "price unknown", no_ratings: "no ratings", sold_out: "sold out",
    delete_pick: "Delete pick {n}", delete_this: "Delete this pick", not_deleted: "The pick could not be deleted.",
    needs_you: "amazon.se needs you: {detail}",
    signin: "amazon.se asks you to sign in", captcha: "amazon.se shows a captcha",
    other_problem: "the page did not load as it should",
    working: "Working", worked: "Worked for {time}", step_one: "1 step", steps: "{n} steps",
    calls: "calls", product_pages: "product pages", web_searches: "web searches",
    sec: "{s} s", min: "{m} min {s} s",
    busy: "A turn is running. Wait until the reply is done.", server: "The server answered {status}.",
    connection: "The connection to the server failed ({error}). Is scout web still running?",
    not_loaded: "The conversation could not be loaded.",
    still_working: "Luna is still working on a message. The reply shows here when it is done.",
    memory_failed: "The memory could not be loaded. Try again later.", nothing: "Nothing yet.",
    like: "like", dislike: "dislike", fact: "fact", all_categories: "all categories",
    delete: "Delete", delete_memory: "Delete: {text}", try_later: "Try later",
  },
  sv: {
    new_chat: "Ny chatt", memory: "Minne", picks: "Förslag",
    chats: "Chattar", close_chats: "Stäng chattarna", no_chats: "Inga chattar än.",
    chats_failed: "Chattarna kunde inte laddas.",
    hero_title: "Vad vill du köpa?",
    hero_text: "Jag söker på amazon.se, kontrollerar varje siffra mot produktsidorna och visar mina bästa förslag.",
    problem_default: "amazon.se behöver dig",
    problem_help: "Logga in eller lös kontrollen i Chrome-fönstret för product-scout och klicka sedan på Klar. Jag skriver aldrig lösenord.",
    problem_page: "Sidan", done: "Klar", skip: "Hoppa över",
    first_hint: "Till exempel: ett kylskåp med frys, under 180 cm, högst 6000 kr",
    next_hint: "Fråga om förslagen, eller säg vad du vill ha", wait_hint: "Vänta på svaret",
    message: "Meddelande", send: "Skicka", close_picks: "Stäng förslagen",
    picks_hint: "Öppna en titel för att se den på amazon.se. Ta bort ett förslag för att hålla det utanför samtalet.",
    removed: "Borttagna", bring_back: "Fråga i chatten för att ta tillbaka ett.",
    memory_title: "Det här minns jag", close_memory: "Stäng minnet",
    memory_hint: "Jag sparar det du säger som fortfarande gäller i en senare chatt, och skälen när du tar bort ett förslag. Ta bort det som är fel.",
    doubtful: "Måttet på sidan stämmer inte med de andra måtten eller volymen",
    no_image: "ingen bild", price_unknown: "pris okänt", no_ratings: "inga betyg", sold_out: "slutsåld",
    delete_pick: "Ta bort förslag {n}", delete_this: "Ta bort det här förslaget",
    not_deleted: "Förslaget kunde inte tas bort.",
    needs_you: "amazon.se behöver dig: {detail}",
    signin: "amazon.se ber dig logga in", captcha: "amazon.se visar en captcha",
    other_problem: "sidan laddades inte som den ska",
    working: "Arbetar", worked: "Arbetade i {time}", step_one: "1 steg", steps: "{n} steg",
    calls: "anrop", product_pages: "produktsidor", web_searches: "webbsökningar",
    sec: "{s} s", min: "{m} min {s} s",
    busy: "En tur pågår. Vänta tills svaret är klart.", server: "Servern svarade {status}.",
    connection: "Anslutningen till servern misslyckades ({error}). Körs scout web fortfarande?",
    not_loaded: "Samtalet kunde inte laddas.",
    still_working: "Luna arbetar fortfarande med ett meddelande. Svaret visas här när det är klart.",
    memory_failed: "Minnet kunde inte laddas. Försök igen senare.", nothing: "Inget än.",
    like: "gillar", dislike: "gillar inte", fact: "faktum", all_categories: "alla kategorier",
    delete: "Ta bort", delete_memory: "Ta bort: {text}", try_later: "Försök senare",
  },
  zh: {
    new_chat: "新对话", memory: "记忆", picks: "推荐",
    chats: "对话", close_chats: "关闭对话列表", no_chats: "还没有对话。",
    chats_failed: "对话列表没能加载。",
    hero_title: "你想买什么？",
    hero_text: "我在 amazon.se 上搜索，把每个数字和商品页核对一遍，然后给出我的推荐。",
    problem_default: "amazon.se 需要你操作",
    problem_help: "请在 product-scout 的 Chrome 窗口里登录或完成验证，然后点“完成”。我从不输入密码。",
    problem_page: "打开页面", done: "完成", skip: "跳过",
    first_hint: "例如：带冷冻室的冰箱，高度不超过 180 cm，最多 6000 kr",
    next_hint: "可以问推荐的问题，或者说说你想要什么", wait_hint: "请等待回复",
    message: "消息", send: "发送", close_picks: "关闭推荐",
    picks_hint: "点标题可在 amazon.se 上查看。删除一个推荐，它就不会再出现在这次对话里。",
    removed: "已删除", bring_back: "想找回的话，在对话里说一声。",
    memory_title: "我记住的内容", close_memory: "关闭记忆",
    memory_hint: "我会记下你说过的、以后对话里仍然成立的内容，以及你删除推荐时的理由。不对的可以删掉。",
    doubtful: "页面上的这个尺寸和其他尺寸或容量对不上",
    no_image: "没有图片", price_unknown: "价格未知", no_ratings: "没有评分", sold_out: "已售罄",
    delete_pick: "删除推荐 {n}", delete_this: "删除这个推荐", not_deleted: "这个推荐没能删除。",
    needs_you: "amazon.se 需要你操作：{detail}",
    signin: "amazon.se 要求登录", captcha: "amazon.se 出现了验证码",
    other_problem: "页面没有正常加载",
    working: "处理中", worked: "用时 {time}", step_one: "1 步", steps: "{n} 步",
    calls: "调用", product_pages: "商品页", web_searches: "网页搜索",
    sec: "{s} 秒", min: "{m} 分 {s} 秒",
    busy: "正在处理上一条消息，请等回复完成。", server: "服务器返回 {status}。",
    connection: "连接服务器失败（{error}）。scout web 还在运行吗？",
    not_loaded: "这次对话没能加载。",
    still_working: "Luna 还在处理一条消息，回复完成后会显示在这里。",
    memory_failed: "记忆没能加载，请稍后再试。", nothing: "还没有内容。",
    like: "喜欢", dislike: "不喜欢", fact: "事实", all_categories: "所有品类",
    delete: "删除", delete_memory: "删除：{text}", try_later: "稍后再试",
  },
};

function browserLang() {
  for (const tag of navigator.languages || [navigator.language || "en"]) {
    const base = tag.toLowerCase().split("-")[0];
    if (base in TEXT) return base;
  }
  return "en";
}

let lang = browserLang();

function tr(key, values = {}) {
  const text = (TEXT[lang] && TEXT[lang][key]) || TEXT.en[key] || key;
  return text.replace(/\{(\w+)\}/g, (_, name) => (name in values ? values[name] : `{${name}}`));
}

// Set the language and the texts of the fixed parts of the page. The hint in the message box changes with the
// state of the page, so setBusy in app.js sets it
function setLang(next) {
  if (!next || !(next in TEXT)) return;
  lang = next;
  document.documentElement.lang = next;
  document.querySelectorAll("[data-t]").forEach((node) => { node.textContent = tr(node.dataset.t); });
  document.querySelectorAll("[data-t-aria]").forEach((node) => node.setAttribute("aria-label", tr(node.dataset.tAria)));
}
