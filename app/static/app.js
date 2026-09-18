const form = document.querySelector("#profile-form");
const queryInput = document.querySelector("#query");
const submitButton = document.querySelector("#submit-button");
const statusBox = document.querySelector("#status");
const resultSection = document.querySelector("#result");

const categoryLabels = {
  campus: "Кампус",
  library: "Библиотека",
  dormitory: "Общежития",
  classroom: "Аудитории",
  student_life: "Студенческая жизнь",
  facilities: "Инфраструктура",
  other: "Другое",
};

const statisticLabels = {
  found: "Найдено",
  duplicates_removed: "Дубликатов удалено",
  verified: "Проверено AI",
  rejected: "Отклонено",
};

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function setLoading(loading) {
  submitButton.disabled = loading;
  submitButton.classList.toggle("loading", loading);
  queryInput.disabled = loading;
}

function showStatus(message, error = false) {
  statusBox.textContent = message;
  statusBox.classList.toggle("error", error);
  statusBox.hidden = false;
}

function renderStatistics(statistics) {
  const container = document.querySelector("#statistics");
  container.replaceChildren();
  Object.entries(statisticLabels).forEach(([key, label]) => {
    const card = element("div", "stat");
    card.append(element("span", "stat-value", String(statistics[key] ?? 0)));
    card.append(element("span", "stat-label", label));
    container.append(card);
  });
}

function renderWarnings(warnings = []) {
  const container = document.querySelector("#warnings");
  container.replaceChildren();
  container.hidden = warnings.length === 0;
  warnings.forEach((warning) => container.append(element("p", "", `⚠ ${warning}`)));
}

function renderImage(image) {
  const card = element("article", "image-card");
  const preview = element("img");
  preview.src = image.image_url;
  preview.alt = image.title || "Фотография университета";
  preview.loading = "eager";
  preview.referrerPolicy = "no-referrer";
  preview.addEventListener("error", () => {
    preview.replaceWith(element("p", "empty", "Источник не отдал превью. Откройте оригинальную страницу по ссылке ниже."));
  }, { once: true });
  card.append(preview);

  const content = element("div", "image-content");
  content.append(element("h4", "image-title", image.title || "Без названия"));
  const badge = element("span", `badge ${image.verification_status}`,
    image.confidence == null ? "Не проверено AI" : image.verification_status);
  content.append(badge);
  if (image.is_primary) content.append(element("span", "badge", "Лучший кадр · Gemini"));
  if (typeof image.confidence === "number") {
    content.append(element("span", "confidence", `${Math.round(image.confidence * 100)}%`));
  }
  if (image.verification_reason) content.append(element("p", "reason", image.verification_reason));
  if (image.is_interesting && image.interest_reason) content.append(element("p", "reason", `Интересная деталь: ${image.interest_reason}`));
  if (image.author) content.append(element("p", "reason", `Автор: ${image.author}`));
  const source = element("a", "source", `Источник: ${image.source_name}`);
  source.href = image.source_url;
  source.target = "_blank";
  source.rel = "noopener noreferrer";
  content.append(source);
  card.append(content);
  return card;
}

function renderCategories(categories) {
  const container = document.querySelector("#categories");
  container.replaceChildren();
  Object.entries(categoryLabels).forEach(([key, label]) => {
    const images = categories[key] || [];
    const section = element("section", "category");
    const header = element("div", "category-header");
    header.append(element("h3", "", label));
    header.append(element("span", "category-count", `${images.length} фото`));
    section.append(header);
    if (images.length === 0) {
      section.append(element("p", "empty", "В этой категории пока нет результатов"));
    } else {
      const grid = element("div", "image-grid");
      images.forEach((image) => grid.append(renderImage(image)));
      section.append(grid);
    }
    container.append(section);
  });
}

function renderProfile(profile) {
  document.querySelector("#university-name").textContent = profile.university.name;
  const location = [profile.university.city, profile.university.country].filter(Boolean).join(", ");
  document.querySelector("#university-location").textContent = location || "Местоположение не указано";

  const domain = document.querySelector("#university-domain");
  if (profile.university.official_domain) {
    domain.textContent = profile.university.official_domain;
    domain.href = `https://${profile.university.official_domain}`;
    domain.hidden = false;
  } else {
    domain.hidden = true;
  }

  renderStatistics(profile.statistics);
  document.querySelector("#resolution-evidence")?.remove();
  if (profile.university.resolution_source) {
    const evidence = element("p", "reason",
      profile.university.resolution_source === "official_website"
        ? "Университет определён по официальному сайту. " : "Университет определён по Wikidata. ");
    evidence.id = "resolution-evidence";
    const url = profile.university.evidence_urls?.[0];
    if (url && /^https?:\/\//.test(url)) {
      const link = element("a", "source", "Источник подтверждения");
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      evidence.append(link);
    }
    domain.parentElement.append(evidence);
  }
  renderWarnings(profile.warnings);
  renderCategories(profile.categories);
  const gallery = document.querySelector("#photo-gallery");
  gallery.replaceChildren();
  gallery.hidden = false;
  const allImages = profile.preliminary_images || [];
  const photos = [...new Map(allImages.filter(i => i.verification_status !== "rejected").map(i => [i.image_url, i])).values()];
  if (photos.length) {
    const details = element("details");
    details.append(element("summary", "", `Не вошли в AI-подборку · ${photos.length}`));
    details.append(element("p", "reason", "Предварительные или недостаточно уверенные результаты. Подборка Gemini — в категориях ниже."));
    const grid = element("div", "image-grid");
    photos.forEach(image => grid.append(renderImage(image)));
    details.append(grid);
    gallery.append(details);
  } else {
    gallery.hidden = true;
  }
  document.querySelector("#generated-at").textContent = `Сформировано: ${new Date(profile.generated_at).toLocaleString("ru-RU")}`;
  resultSection.hidden = false;
  resultSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

function errorMessage(response, body) {
  const status = body?.detail?.status;
  if (response.status === 404 || status === "not_found") return "Университет не найден. Проверьте название и попробуйте снова.";
  if (response.status === 409 || status === "ambiguous") return "Название неоднозначно. Уточните полное название университета или город.";
  if (response.status === 504) return "Генерация заняла слишком много времени. Попробуйте ещё раз.";
  if (response.status === 503) return "Источник сведений об университете временно недоступен. Попробуйте повторить запрос.";
  return "Не удалось создать профиль. Проверьте backend и попробуйте снова.";
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = queryInput.value.trim();
  if (!query) return;

  setLoading(true);
  resultSection.hidden = true;
  showStatus("Ищем университет и фотографии, затем проверяем их через Gemini…");
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30000);

  try {
    const response = await fetch("/api/profile/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
      signal: controller.signal,
    });
    const body = await response.json();
    if (!response.ok) throw { response, body };
    statusBox.hidden = true;
    renderProfile(body);
  } catch (error) {
    if (error?.name === "AbortError") {
      showStatus("Превышено время ожидания. Backend мог продолжить обработку — повторите запрос, чтобы проверить cache.", true);
    } else if (error?.response) {
      showStatus(errorMessage(error.response, error.body), true);
    } else {
      showStatus("Нет соединения с backend. Убедитесь, что сервер запущен.", true);
    }
  } finally {
    clearTimeout(timeout);
    setLoading(false);
  }
});
