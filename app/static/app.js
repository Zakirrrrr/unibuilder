const screens = Object.fromEntries(["search", "loading", "results"].map(name => [name, document.getElementById("screen-" + name)]));
const names = {campus:"Кампус",dormitory:"Общежития",classroom:"Аудитории",library:"Библиотеки",student_life:"Студенческая жизнь",facilities:"Инфраструктура",sport:"Спорт",laboratory:"Лаборатории",city:"Город",other:"Другое"};
const form = document.getElementById("searchForm");
const input = document.getElementById("searchInput");
const errorBox = document.getElementById("searchError");
const candidatesBox = document.getElementById("resultsList");
const circumference = 2 * Math.PI * 65;
const loadingMessages = [
  "🔎 Ищем университеты...",
  "🌍 Изучаем кампусы по всему миру...",
  "🏛️ Заглядываем в университеты...",
  "📸 Собираем фотографии...",
  "🖼️ Проверяем качество снимков...",
  "🧹 Удаляем дубликаты...",
  "🗂️ Делим на категории...",
  "🧩 Сопоставляем фото и университеты...",
  "🏠 Ищем фотографии общежитий...",
  "📚 Подбираем фотографии библиотек...",
  "🔬 Заглядываем в лаборатории...",
  "🤔 Хм, кажется, нашли кое-что интересное...",
  "🕵️ Ищем скрытые жемчужины...",
  "☕ Наши алгоритмы уже выпили кофе...",
  "✨ Наводим красоту...",
];
let aborter, ticker, messageTicker, requestNumber = 0, currentProfile, selectedCategory;
const popupLayer = document.getElementById("loadingPopups");
const popupItems = [];
const popupGap = 18;

function shuffledMessages() {
  const messages = [...loadingMessages];
  for (let i = messages.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [messages[i], messages[j]] = [messages[j], messages[i]];
  }
  return messages;
}
function findPopupPosition(width, height, area, blocked, random = Math.random) {
  const fits = (x, y) => x >= 16 && y >= 16 && x + width <= area.width - 16 &&
    y + height <= area.height - 16 && blocked.every(rect =>
      x + width + popupGap <= rect.x || x >= rect.x + rect.width + popupGap ||
      y + height + popupGap <= rect.y || y >= rect.y + rect.height + popupGap);
  const maxX = area.width - width - 16, maxY = area.height - height - 16;
  if (maxX < 16 || maxY < 16) return null;
  for (let attempt = 0; attempt < 120; attempt++) {
    const x = 16 + random() * (maxX - 16), y = 16 + random() * (maxY - 16);
    if (fits(x, y)) return {x, y};
  }
  const available = [];
  for (let y = 16; y <= maxY; y += 12) {
    for (let x = 16; x <= maxX; x += 12) {
      if (fits(x, y)) available.push({x, y});
    }
  }
  return available.length ? available[Math.floor(random() * available.length)] : null;
}
function placePopup(element, previous) {
  const area = screens.loading.getBoundingClientRect();
  const core = document.getElementById("loadingCore").getBoundingClientRect();
  const blocked = [
    {x:core.left-area.left,y:core.top-area.top,width:core.width,height:core.height},
    ...previous.map(item => ({
      x:parseFloat(item.style.left),y:parseFloat(item.style.top),
      width:item.offsetWidth,height:item.offsetHeight
    }))
  ];
  const width = element.offsetWidth, height = element.offsetHeight;
  let position = findPopupPosition(width, height, area, blocked);
  if (!position) {
    // Small screens gain scrollable space instead of overlapping messages.
    position = {
      x:16 + Math.random() * Math.max(0, area.width-width-32),
      y:Math.max(area.height, ...blocked.map(rect => rect.y+rect.height)) + popupGap
    };
    screens.loading.style.minHeight = (position.y + height + 16) + "px";
  }
  element.style.left = position.x + "px";
  element.style.top = position.y + "px";
}
function addLoadingPopup(message) {
  const element = document.createElement("p");
  element.className = "loading-popup";
  element.textContent = message;
  popupLayer.append(element);
  placePopup(element, popupItems);
  popupItems.push(element);
}
function reflowLoadingPopups() {
  if (!screens.loading.classList.contains("active")) return;
  screens.loading.style.minHeight = "";
  popupItems.forEach((element,index) => placePopup(element,popupItems.slice(0,index)));
}
window.addEventListener("resize", reflowLoadingPopups);
document.fonts?.ready.then(reflowLoadingPopups);

function screen(name) {
  Object.values(screens).forEach(element => element.classList.remove("active"));
  screens[name].classList.add("active");
  window.scrollTo(0, 0);
}
function errorText(value) { errorBox.textContent = value; errorBox.hidden = !value; }
function safeUrl(value) {
  try { const url = new URL(value); return ["http:", "https:"].includes(url.protocol) ? url.href : null; }
  catch { return null; }
}
function stopLoadingTimers() {
  if (ticker) clearInterval(ticker);
  if (messageTicker) clearInterval(messageTicker);
  ticker = undefined;
  messageTicker = undefined;
}
function clearLoading() {
  stopLoadingTimers();
  popupLayer.replaceChildren();
  popupItems.length = 0;
  screens.loading.style.minHeight = "";
  aborter = undefined;
}
function loading(name) {
  document.getElementById("loadingUniName").textContent = name;
  const status = document.getElementById("loadingStatus");
  status.textContent = "";
  screen("loading");
  const messages = shuffledMessages();
  let messageIndex = 0;
  addLoadingPopup(messages[messageIndex]);
  messageTicker = setInterval(() => {
    messageIndex++;
    addLoadingPopup(messages[messageIndex]);
    if (messageIndex === messages.length - 1) {
      clearInterval(messageTicker);
      messageTicker = undefined;
    }
  }, 4000);
  const ring = document.getElementById("ringFg");
  const clock = document.getElementById("ringPct");
  ring.style.strokeDasharray = String(circumference);
  const start = performance.now();
  const update = () => {
    const elapsed = Math.min(performance.now() - start, 30000);
    clock.textContent = Math.max(0, Math.ceil((30000 - elapsed) / 1000)) + " с";
    ring.style.strokeDashoffset = String(circumference * elapsed / 30000);
  };
  update();
  ticker = setInterval(update, 100);
}
function finishLoading(signal) {
  stopLoadingTimers();
  document.getElementById("loadingStatus").textContent = "⏳ Еще чуть-чуть...";
  document.getElementById("ringPct").textContent = "✓";
  document.getElementById("ringFg").style.strokeDashoffset = "0";
  return new Promise((resolve, reject) => {
    const onAbort = () => {
      clearTimeout(delay);
      reject(new DOMException("Search cancelled", "AbortError"));
    };
    const delay = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, 1200);
    signal.addEventListener("abort", onAbort, {once:true});
    if (signal.aborted) onAbort();
  });
}
function cancel() {
  requestNumber++;
  aborter?.abort();
  clearLoading();
  screen("search");
}
function explainFailure(reason) {
  if (reason.name === "AbortError") return "Поиск не завершился за 30 секунд. Попробуйте ещё раз.";
  if (reason.status === 404) return "Университет не найден. Уточните название.";
  if (reason.status === 503) return "Источник данных временно недоступен. Попробуйте позже.";
  if (reason.status === 504) return "Поиск занял слишком много времени. Попробуйте ещё раз.";
  return "Не удалось создать профиль. Проверьте подключение и повторите поиск.";
}
function showCandidates(candidates, query) {
  candidatesBox.replaceChildren();
  candidates.forEach(uni => {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "result-row candidate-button";
    const title = document.createElement("span");
    title.className = "result-name";
    title.textContent = uni.name;
    const location = document.createElement("span");
    location.className = "result-city";
    location.textContent = [uni.city, uni.country].filter(Boolean).join(", ");
    row.append(title, location);
    row.addEventListener("click", () => generate(query, uni.id));
    candidatesBox.append(row);
  });
  candidatesBox.classList.add("open");
  errorText("Найдено несколько университетов. Выберите нужный.");
  screen("search");
}
async function generate(query, selectedId = null) {
  if (aborter) aborter.abort();
  clearLoading();
  errorText("");
  candidatesBox.classList.remove("open");
  const number = ++requestNumber;
  const activeController = new AbortController();
  aborter = activeController;
  const signal = activeController.signal;
  const deadline = setTimeout(() => activeController.abort(), 30000);
  loading(query);
  try {
    const response = await fetch("/api/profile/generate", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({query, selected_university_id:selectedId}), signal
    });
    const body = await response.json();
    if (number !== requestNumber) return;
    if (!response.ok) {
      const reason = new Error("HTTP " + response.status);
      reason.status = response.status;
      reason.detail = body.detail;
      throw reason;
    }
    render(body);
    clearTimeout(deadline);
    await finishLoading(signal);
    if (number !== requestNumber) return;
    screen("results");
  } catch (reason) {
    if (number !== requestNumber) return;
    if (reason.status === 409 && Array.isArray(reason.detail?.candidates)) showCandidates(reason.detail.candidates, query);
    else { errorText(explainFailure(reason)); screen("search"); }
  } finally {
    clearTimeout(deadline);
    if (number === requestNumber) clearLoading();
  }
}
form.addEventListener("submit", event => {
  event.preventDefault();
  const query = input.value.trim();
  if (query) generate(query);
});
input.addEventListener("input", () => { errorText(""); candidatesBox.classList.remove("open"); });
document.getElementById("cancelBtn").addEventListener("click", cancel);
document.getElementById("backBtn").addEventListener("click", () => { cancel(); input.focus(); });

function render(body) {
  currentProfile = body;
  const university = body.university || {};
  document.getElementById("resultsUniName").textContent = university.name || "Университет";
  const photos = Object.values(body.categories || {}).flat().concat(
    (body.preliminary_images || []).filter(photo =>
      photo.verification_status !== "rejected" &&
      photo.is_real_photo !== false &&
      photo.is_relevant !== false
    )
  );
  const sources = new Set(photos.map(photo => safeUrl(photo.source_url)).filter(Boolean));
  const place = [university.city, university.country].filter(Boolean).join(", ");
  document.getElementById("resultsUniMeta").textContent = (place || "Местоположение не указано") + " · " + sources.size + " источников фотографий";
  const categories = Object.keys(body.categories || {}).filter(key =>
    body.categories[key].length + preliminaryFor(key).length > 0
  );
  selectedCategory = categories[0] || null;
  renderTabs(categories);
  renderGallery();
  renderDetails(body);
}
function renderTabs(categories) {
  const tabs = document.getElementById("tabs");
  tabs.replaceChildren();
  tabs.hidden = categories.length === 0;
  categories.forEach(key => {
    const tab = document.createElement("button");
    tab.type = "button";
    tab.className = "tab" + (key === selectedCategory ? " active" : "");
    const count = currentProfile.categories[key].length + preliminaryFor(key).length;
    tab.textContent = (names[key] || key) + " (" + count + ")";
    tab.addEventListener("click", () => { selectedCategory = key; renderTabs(categories); renderGallery(); });
    tabs.append(tab);
  });
}
function preliminaryFor(category) {
  return (currentProfile.preliminary_images || []).filter(photo =>
    photo.verification_status !== "rejected" &&
    photo.is_real_photo !== false &&
    photo.is_relevant !== false &&
    (names[photo.category] ? photo.category : "other") === category
  );
}
function makePhoto(photo, category, preliminary = false) {
  const card = document.createElement("article");
  card.className = "photo-card";
  const url = safeUrl(photo.image_url);
  if (url) {
    const image = document.createElement("img");
    image.className = "photo-image";
    image.src = url;
    image.alt = photo.title || ((names[category] || category) + ": " + currentProfile.university.name);
    image.loading = "lazy";
    image.referrerPolicy = "no-referrer";
    card.append(image);
  }
  const info = document.createElement("div");
  info.className = "photo-info";
  const badge = document.createElement("span");
  badge.className = "photo-cat";
  const status = photo.verification_status === "confirmed" ? "Подтверждено" : photo.verification_status === "likely" ? "Вероятно" : "Не подтверждено";
  badge.textContent = (names[category] || category) + " · " + status;
  if (preliminary && photo.verification_status === "uncertain") {
    badge.title = "Категория определена предварительно по поисковому запросу";
  }
  info.append(badge);
  const source = safeUrl(photo.source_url);
  if (source) {
    const link = document.createElement("a");
    link.className = "photo-source";
    link.href = source; link.target = "_blank"; link.rel = "noopener noreferrer";
    link.textContent = photo.source_name || "Источник фотографии";
    info.append(link);
  }
  if (photo.published_at || photo.retrieved_at) {
    const date = document.createElement("span");
    date.className = "photo-date";
    date.textContent = photo.published_at ? "Опубликовано: " + photo.published_at : "Получено: " + photo.retrieved_at;
    info.append(date);
  }
  card.append(info);
  return card;
}
function renderGallery() {
  const gallery = document.getElementById("gallery");
  gallery.replaceChildren();
  const verified = selectedCategory ? currentProfile.categories[selectedCategory] : [];
  const preliminary = selectedCategory ? preliminaryFor(selectedCategory) : [];
  if (!verified.length && !preliminary.length) {
    const empty = document.createElement("p");
    empty.className = "empty-hint";
    empty.textContent = "Фотографии пока не найдены.";
    gallery.append(empty);
  } else {
    verified.forEach(photo => gallery.append(makePhoto(photo, selectedCategory)));
    preliminary.forEach(photo => gallery.append(makePhoto(photo, selectedCategory, true)));
  }
}
function renderDetails(body) {
  const details = document.getElementById("profileDetails");
  details.replaceChildren();
  if (body.campus_summary) {
    const title = document.createElement("h3"); title.textContent = "О кампусе";
    const text = document.createElement("p"); text.textContent = body.campus_summary;
    details.append(title, text);
    (body.summary_sources || []).forEach(raw => {
      const url = safeUrl(raw);
      if (!url) return;
      const link = document.createElement("a");
      link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer";
      link.textContent = "Источник описания";
      details.append(link);
    });
  }
  details.hidden = !details.childNodes.length;
}
