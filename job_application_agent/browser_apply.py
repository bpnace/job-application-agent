from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from .application import classify_form_field, extract_form_fields_from_html
from .models import ApplicationFormField, FormFillInstruction, FormFillPlan
from .portal import classify_portal_state, portal_evidence, submission_blockers


DOM_FIELDS_SCRIPT = r"""
() => {
  const blockedTypes = new Set(["hidden", "submit", "button", "reset", "image"]);
  const out = [];
  const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const esc = (value) => {
    if (globalThis.CSS && typeof globalThis.CSS.escape === "function") return globalThis.CSS.escape(value);
    return String(value).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  };
  const selectorFor = (el, type) => {
    if (el.id) return /^[A-Za-z_][A-Za-z0-9_-]*$/.test(el.id) ? `#${el.id}` : `[id="${esc(el.id)}"]`;
    if (el.name) {
      if (type === "checkbox" || type === "radio") {
        const value = clean(el.value);
        if (value) return `input[type="${type}"][name="${esc(el.name)}"][value="${esc(value)}"]`;
        return `input[type="${type}"][name="${esc(el.name)}"]`;
      }
      return `[name="${esc(el.name)}"]`;
    }
    const path = [];
    let node = el;
    while (node && node.nodeType === 1 && node.tagName && node.tagName.toLowerCase() !== "html") {
      const tag = node.tagName.toLowerCase();
      const parent = node.parentElement;
      if (!parent) {
        path.unshift(tag);
        break;
      }
      const sameTagSiblings = Array.from(parent.children).filter((child) => child.tagName === node.tagName);
      const index = sameTagSiblings.indexOf(node) + 1;
      path.unshift(`${tag}:nth-of-type(${index})`);
      node = parent;
      if (path.length >= 5) break;
    }
    return path.join(" > ") || el.tagName.toLowerCase();
  };
  const labelFor = (root, el) => {
    if (el.labels && el.labels.length) return clean(Array.from(el.labels).map((label) => label.innerText).join(" "));
    if (el.id) {
      const label = root.querySelector(`label[for="${esc(el.id)}"]`);
      if (label) return clean(label.innerText);
    }
    const parentLabel = el.closest("label");
    if (parentLabel) return clean(parentLabel.innerText);
    const fieldset = el.closest("fieldset");
    const legend = fieldset ? fieldset.querySelector("legend") : null;
    if (legend) return clean(legend.innerText);
    return clean(el.getAttribute("aria-label") || el.getAttribute("placeholder") || el.name || el.id);
  };
  const optionsFor = (root, el, type) => {
    if (el.tagName.toLowerCase() === "select") {
      return Array.from(el.options || []).map((option) => clean(option.text || option.value)).filter(Boolean);
    }
    if ((type === "checkbox" || type === "radio") && el.name) {
      return Array.from(root.querySelectorAll(`input[type="${type}"][name="${esc(el.name)}"]`))
        .map((input) => labelFor(root, input) || clean(input.value))
        .filter(Boolean);
    }
    return [];
  };
  const nearbyText = (root, el) => {
    const parts = [];
    const describedBy = clean(el.getAttribute("aria-describedby"));
    if (describedBy) {
      describedBy.split(/\s+/).forEach((id) => {
        const node = root.getElementById ? root.getElementById(id) : root.querySelector(`#${esc(id)}`);
        if (node) parts.push(clean(node.innerText || node.textContent));
      });
    }
    [el.previousElementSibling].forEach((node) => {
      const text = clean(node && (node.innerText || node.textContent));
      if (text && text.length <= 300) parts.push(text);
    });
    const parent = el.parentElement;
    if (parent && !["FORM", "BODY", "HTML"].includes(parent.tagName)) {
      const text = clean(parent.innerText || parent.textContent);
      if (text && text.length <= 500) parts.push(text);
    }
    return clean(parts.join(" "));
  };
  const visible = (el) => {
    const style = globalThis.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
  };
  const visit = (root) => {
    root.querySelectorAll("input, textarea, select").forEach((el) => {
      const tag = el.tagName.toLowerCase();
      const type = clean(el.getAttribute("type") || tag || "text").toLowerCase();
      if (blockedTypes.has(type)) return;
      const nearby = nearbyText(root, el);
      const label = labelFor(root, el) || nearby;
      const context = clean([
        label,
        nearby,
        el.name,
        el.id,
        el.getAttribute("aria-label"),
        el.getAttribute("aria-describedby"),
        el.getAttribute("placeholder"),
        el.getAttribute("autocomplete"),
      ].join(" "));
      const required = Boolean(el.required)
        || String(el.getAttribute("aria-required") || "").toLowerCase() === "true"
        || /\brequired\b|\bpflicht\b|erforderlich|\*/i.test(context);
      out.push({
        label,
        name: clean(el.name),
        selector: selectorFor(el, type),
        field_type: type,
        required,
        options: optionsFor(root, el, type),
        placeholder: clean(el.getAttribute("placeholder")),
        autocomplete: clean(el.getAttribute("autocomplete")),
        aria_label: clean(el.getAttribute("aria-label")),
        context_text: context,
        visible: visible(el),
        disabled: Boolean(el.disabled) || String(el.getAttribute("aria-disabled") || "").toLowerCase() === "true",
        group_name: (type === "checkbox" || type === "radio") ? clean(el.name) : "",
      });
    });
    root.querySelectorAll("*").forEach((el) => {
      if (el.shadowRoot) visit(el.shadowRoot);
    });
  };
  visit(document);
  return out;
}
"""


def _field_from_dom(raw: dict[str, Any], frame_url: str) -> ApplicationFormField:
    label = str(raw.get("label") or "")
    name = str(raw.get("name") or "")
    field_type = str(raw.get("field_type") or "text")
    context = " ".join(
        str(raw.get(key) or "")
        for key in [
            "label",
            "name",
            "placeholder",
            "autocomplete",
            "aria_label",
            "context_text",
        ]
    )
    options = [str(option) for option in raw.get("options", []) if option]
    classification = classify_form_field(
        " ".join([context, " ".join(options)]), name, field_type
    )
    return ApplicationFormField(
        label=label or name,
        name=name,
        selector=str(raw.get("selector") or ""),
        field_type=field_type,
        required=bool(raw.get("required")),
        options=options,
        classification=classification,
        placeholder=str(raw.get("placeholder") or ""),
        autocomplete=str(raw.get("autocomplete") or ""),
        aria_label=str(raw.get("aria_label") or ""),
        visible=bool(raw.get("visible", True)),
        disabled=bool(raw.get("disabled")),
        frame_url=frame_url,
        group_name=str(raw.get("group_name") or ""),
        requires_manual_review=classification
        in {
            "salary",
            "availability",
            "work_authorization",
            "relocation",
            "eeo",
            "screening_question",
            "consent",
        },
    )


def inspect_form_fields_with_playwright(
    url: str, headless: bool = True, timeout_ms: int = 20000
) -> list[ApplicationFormField]:
    from playwright.sync_api import Error, sync_playwright

    fields: list[ApplicationFormField] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(1000)
        _open_application_form(page, url)
        seen: set[tuple[str, str, str]] = set()
        for frame in page.frames:
            try:
                raw_fields = frame.evaluate(DOM_FIELDS_SCRIPT)
            except Error:
                continue
            for raw in raw_fields:
                if not isinstance(raw, dict):
                    continue
                field = _field_from_dom(raw, frame.url)
                key = (field.frame_url, field.selector, field.name)
                if key in seen:
                    continue
                seen.add(key)
                fields.append(field)
        if not fields:
            html = page.content()
            fields = extract_form_fields_from_html(html)
        browser.close()
    return fields


def probe_public_form_read_only(
    url: str, *, headless: bool = True, timeout_ms: int = 20000
) -> dict[str, Any]:
    """Inspect a public form in a fresh context while aborting every non-GET request.

    No candidate profile is loaded here.  The probe performs no fill, upload, or
    submit operation and tears down all browser state at the end.
    """
    from playwright.sync_api import Error, sync_playwright

    blocked: list[dict[str, str]] = []
    fields: list[ApplicationFormField] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context()

        def guard(route: Any) -> None:
            request = route.request
            if request.method.upper() != "GET":
                blocked.append({"method": request.method, "url": request.url})
                route.abort()
                return
            route.continue_()

        context.route("**/*", guard)
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(500)
            _open_application_form(page, url)
            page.wait_for_timeout(500)
            state = classify_portal_state(url=page.url, page_text=page.locator("body").inner_text(timeout=3000))
            seen: set[tuple[str, str, str]] = set()
            for frame in page.frames:
                try:
                    raw_fields = frame.evaluate(DOM_FIELDS_SCRIPT)
                except Error:
                    continue
                for raw in raw_fields:
                    if not isinstance(raw, dict):
                        continue
                    field = _field_from_dom(raw, frame.url)
                    key = (field.frame_url, field.selector, field.name)
                    if key not in seen:
                        seen.add(key)
                        fields.append(field)
            return {
                "status": "probed",
                "read_only": True,
                "reachable": True,
                "final_url": page.url,
                "portal_state": state["state"],
                "portal_reason": state["reason"],
                "fields": [field.model_dump(mode="json") for field in fields],
                "field_count": len(fields),
                "blocked_non_get_requests": blocked,
                "filled_fields": 0,
                "uploads": 0,
                "submit_attempted": False,
            }
        except Exception as exc:
            return {
                "status": "unreachable",
                "read_only": True,
                "reachable": False,
                "portal_state": "unknown",
                "portal_reason": str(exc).splitlines()[0][:180],
                "fields": [],
                "field_count": 0,
                "blocked_non_get_requests": blocked,
                "filled_fields": 0,
                "uploads": 0,
                "submit_attempted": False,
            }
        finally:
            context.close()
            browser.close()


def _target_frame(page: Any, frame_url: str) -> Any:
    if not frame_url:
        return page
    for frame in page.frames:
        if frame.url == frame_url:
            return frame
    for frame in page.frames:
        if frame_url in frame.url or frame.url in frame_url:
            return frame
    return page


def _open_application_form(page: Any, url: str) -> None:
    if "jobs.personio." in url.lower() and "apply" not in url.lower():
        current = page.url
        separator = "&" if "?" in current else "?"
        page.goto(f"{current}{separator}apply", wait_until="domcontentloaded")
        page.wait_for_timeout(1000)
        return
    apply_name = re.compile(
        r"apply(?: for this job| now)?|auf diese stelle bewerben|jetzt bewerben",
        re.I,
    )
    candidates = [
        page.get_by_role("link", name=apply_name).first,
        page.get_by_role("button", name=apply_name).first,
    ]
    for locator in candidates:
        try:
            if locator.count() == 0:
                continue
            locator.wait_for(state="visible", timeout=3000)
            locator.click(timeout=3000)
            page.wait_for_timeout(1000)
            return
        except Exception:
            continue


def _click_submit_control(page: Any) -> dict[str, str]:
    submit_name = re.compile(
        r"submit|send application|apply now|bewerbung absenden|bewerbung senden|absenden|senden|jetzt bewerben",
        re.I,
    )
    candidates = [
        page.get_by_role("button", name=submit_name).last,
        page.locator(
            'form button[type="submit"]:visible, form input[type="submit"]:visible'
        ).last,
        page.locator('button[type="submit"]:visible, input[type="submit"]:visible').last,
        page.locator('button[type="submit"], input[type="submit"]').last,
    ]
    for locator in candidates:
        try:
            if locator.count() == 0:
                continue
            locator.wait_for(state="visible", timeout=5000)
            deadline = time.monotonic() + 15
            while not locator.is_enabled() and time.monotonic() < deadline:
                page.wait_for_timeout(500)
            if not locator.is_enabled():
                last_error = "submit control stayed disabled"
                continue
            locator.click(timeout=5000)
            return {"submit": "clicked"}
        except Exception as exc:
            last_error = str(exc)[:160]
            try:
                if not locator.is_visible() or not locator.is_enabled():
                    continue
                locator.click(timeout=5000, force=True)
                return {"submit": "clicked_force"}
            except Exception as force_exc:
                last_error = str(force_exc)[:160]
    return {
        "submit": f"not_clicked: {locals().get('last_error', 'no submit control found')}"
    }


def _join_completion_guard(plan: FormFillPlan) -> dict[str, str]:
    route_platform = plan.route.platform.casefold()
    apply_url = plan.apply_url.casefold()
    if (
        "join.com" not in apply_url
        and "arbeitnow.com" not in apply_url
        and route_platform
        not in {
            "join",
            "arbeitnow",
        }
    ):
        return {}
    return {
        "join_completion_status": "final_confirmation_required",
        "join_completion_note": (
            "JOIN/Arbeitnow can require multiple Weiter/Continue steps. "
            "Expected JOIN flow is CV upload, Weiter, cover-letter upload, Weiter, "
            "start date, individual questions and salary, then the final Zustimmen & Bewerben button. "
            "Do not mark this job applied after the first click, first upload, or first POST. "
            "Keep needs_completion until the final JOIN confirmation is reached and no completion email is outstanding."
        ),
    }


def evaluate_submit_evidence(result: dict[str, Any]) -> dict[str, str | bool]:
    submit_requested = bool(result.get("submit_requested"))
    submit_allowed = bool(result.get("submit_allowed"))
    submit_state = str(result.get("submit") or "")
    portal_state = str(result.get("portal_state") or "")
    page_text = str(result.get("page_text_excerpt") or "")
    final_url = str(result.get("final_url") or "")
    validation = result.get("validation")
    responses = result.get("responses")
    joined_text = " ".join(
        [
            page_text,
            final_url,
            submit_state,
            _validation_text(validation),
            _response_text(responses),
        ]
    ).lower()
    if portal_state == "blocked_captcha":
        return {
            "application_status": "blocked_captcha",
            "tracker_status": "needs_completion",
            "submit_evidence_level": "captcha_blocked",
            "status_reason": str(result.get("portal_reason") or "CAPTCHA detected."),
            "status_event_recommended": True,
        }
    if portal_state == "needs_completion":
        return {
            "application_status": "needs_completion",
            "tracker_status": "needs_completion",
            "submit_evidence_level": "portal_gate",
            "status_reason": str(result.get("portal_reason") or "Portal requires human completion."),
            "status_event_recommended": True,
        }
    if (
        not submit_requested
        or not submit_allowed
        or submit_state.startswith(("blocked", "not_clicked"))
    ):
        return {
            "application_status": "not_submitted",
            "tracker_status": "",
            "submit_evidence_level": "none",
            "status_reason": "Submit was not requested, not allowed, or no submit control was clicked.",
            "status_event_recommended": False,
        }
    if _has_captcha_signal(joined_text):
        return {
            "application_status": "blocked_captcha",
            "tracker_status": "needs_completion",
            "submit_evidence_level": "captcha_blocked",
            "status_reason": "CAPTCHA or anti-bot challenge detected after submit attempt.",
            "status_event_recommended": True,
        }
    if validation:
        return {
            "application_status": "needs_completion",
            "tracker_status": "needs_completion",
            "submit_evidence_level": "validation_blocked",
            "status_reason": "The page still reports invalid or required fields after submit.",
            "status_event_recommended": True,
        }
    if _has_application_error_response(responses):
        return {
            "application_status": "needs_completion",
            "tracker_status": "needs_completion",
            "submit_evidence_level": "post_error",
            "status_reason": "Application-like POST returned an error status; final submission was not confirmed.",
            "status_event_recommended": True,
        }
    if result.get("join_completion_status") == "final_confirmation_required":
        return {
            "application_status": "needs_completion",
            "tracker_status": "needs_completion",
            "submit_evidence_level": "intermediate",
            "status_reason": "JOIN/Arbeitnow flow requires final confirmation beyond the first submit/continue click.",
            "status_event_recommended": True,
        }
    if _has_final_success_signal(joined_text):
        return {
            "application_status": "applied",
            "tracker_status": "applied",
            "submit_evidence_level": "final_confirmation",
            "status_reason": "Final success or thank-you confirmation was visible after submit.",
            "status_event_recommended": True,
        }
    if _has_success_response_signal(responses):
        return {
            "application_status": "applied",
            "tracker_status": "applied",
            "submit_evidence_level": "post_success",
            "status_reason": "Application-like POST returned a successful HTTP status and no final-step blocker was detected.",
            "status_event_recommended": True,
        }
    if _has_intermediate_step_signal(joined_text):
        return {
            "application_status": "needs_completion",
            "tracker_status": "needs_completion",
            "submit_evidence_level": "intermediate",
            "status_reason": "The resulting page looks like an intermediate application step, not a final confirmation.",
            "status_event_recommended": True,
        }
    return {
        "application_status": "in_progress",
        "tracker_status": "in_progress",
        "submit_evidence_level": "click_only",
        "status_reason": "Submit was clicked, but no final success proof was captured.",
        "status_event_recommended": True,
    }


def _validation_text(validation: Any) -> str:
    if not isinstance(validation, list):
        return ""
    parts: list[str] = []
    for item in validation:
        if not isinstance(item, dict):
            continue
        parts.extend(str(item.get(key) or "") for key in ["label", "message", "name"])
    return " ".join(parts)


def _response_text(responses: Any) -> str:
    if not isinstance(responses, list):
        return ""
    parts: list[str] = []
    for item in responses:
        if not isinstance(item, dict):
            continue
        parts.extend(str(item.get(key) or "") for key in ["method", "status", "url"])
    return " ".join(parts)


def _has_captcha_signal(text: str) -> bool:
    return bool(
        re.search(
            r"captcha|hcaptcha|recaptcha|anti[- ]bot|robot check|"
            r"verify you are human|428|challenges\.cloudflare\.com|"
            r"/cdn-cgi/challenge-platform/.*/flow/",
            text,
            re.I,
        )
    )


def _has_final_success_signal(text: str) -> bool:
    return bool(
        re.search(
            r"application (?:submitted|received|success)|application\.success|"
            r"thank you for (?:applying|your application)|thanks for applying|"
            r"bewerbung (?:eingegangen|erfolgreich|versendet|gesendet)|"
            r"vielen dank für (?:deine|ihre) bewerbung|"
            r"danke für (?:deine|ihre) bewerbung",
            text,
            re.I,
        )
    )


def _has_intermediate_step_signal(text: str) -> bool:
    return bool(
        re.search(
            r"/apply/(?:cv|questions|cover|authentication)|confirm your cv|"
            r"upload (?:your )?(?:cv|resume|cover letter)|weiter|continue|next step|"
            r"start date|salary expectation|screening questions?",
            text,
            re.I,
        )
    )


def _has_success_response_signal(responses: Any) -> bool:
    if not isinstance(responses, list):
        return False
    ignored_hosts = (
        "analytics.google.com",
        "google-analytics.com",
        "doubleclick.net",
        "cloudflare.com",
        "s3.",
        "sentry.io",
        "cloudfront.net",
    )
    for item in responses:
        if not isinstance(item, dict):
            continue
        method = str(item.get("method") or "").upper()
        try:
            status = int(item.get("status") or 0)
        except (TypeError, ValueError):
            status = 0
        url = str(item.get("url") or "").lower()
        if method == "GET" or not (200 <= status < 300):
            continue
        if any(host in url for host in ignored_hosts):
            continue
        if re.search(
            r"send_application|applysubmit|/application(?:[/?#]|$)|"
            r"/apply(?:[/?#]|$)|/candidates?(?:[/?#]|$)|"
            r"jobapply|candidate",
            url,
        ):
            return True
    return False


def _has_application_error_response(responses: Any) -> bool:
    if not isinstance(responses, list):
        return False
    for item in responses:
        if not isinstance(item, dict):
            continue
        method = str(item.get("method") or "").upper()
        try:
            status = int(item.get("status") or 0)
        except (TypeError, ValueError):
            status = 0
        url = str(item.get("url") or "").lower()
        if method == "GET" or status < 400:
            continue
        if re.search(
            r"send_application|applysubmit|/application(?:[/?#]|$)|"
            r"/apply(?:[/?#]|$)|/candidates?(?:[/?#]|$)|"
            r"jobapply|candidate",
            url,
        ):
            return True
    return False


def _remove_known_interaction_blockers(page: Any) -> None:
    script = """
    () => {
      const cmp = document.querySelector('#cmpwrapper');
      if (cmp && !String(cmp.innerText || '').trim()) cmp.remove();
      document.querySelectorAll('[aria-hidden="true"].modal, .modal-backdrop, .cookiebot, .cookie-banner').forEach((node) => {
        const text = String(node.innerText || '').trim();
        if (!text) node.remove();
      });
    }
    """
    for frame in page.frames:
        try:
            frame.evaluate(script)
        except Exception:
            continue


def _check_control(locator: Any) -> str:
    try:
        locator.check(timeout=5000)
        return "filled"
    except Exception:
        try:
            locator.check(timeout=5000, force=True)
            return "filled_force"
        except Exception:
            locator.evaluate(
                """(el) => {
                  el.checked = true;
                  el.dispatchEvent(new Event('input', { bubbles: true }));
                  el.dispatchEvent(new Event('change', { bubbles: true }));
                }"""
            )
            return "filled_dom"


def _combobox_candidate_terms(value: str, field_label: str = "") -> list[str]:
    terms = [value]
    normalized = value.lower().strip()
    label = field_label.lower()
    if normalized in {"deutschland", "de", "germany"}:
        terms.insert(0, "Germany")
    if "authorized to work" in normalized or "visa required" in normalized:
        terms.insert(0, "Yes")
    if normalized.startswith("yes,") or normalized == "yes":
        if any(marker in label for marker in ["privacy", "talent pool", "consent"]):
            terms.insert(0, "I confirm!")
            terms.insert(1, "Yes")
        else:
            terms.insert(0, "Yes")
            terms.insert(1, "I confirm!")
    if normalized in {"no visa required.", "not applicable.", "not applicable"}:
        terms.insert(0, "Not applicable")
    if normalized in {"prefer not to say", "i prefer not to say"}:
        terms.insert(0, "I don't wish to answer")
    return list(dict.fromkeys(term for term in terms if term))


def _attr_selector(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'[id="{escaped}"]'


def _click_combobox_option(page: Any, locator: Any, term: str) -> bool:
    desired = re.sub(r"[^a-z0-9]+", " ", term.lower()).strip()
    listbox_id = ""
    try:
        listbox_id = str(locator.get_attribute("aria-controls") or "")
    except Exception:
        listbox_id = ""
    if listbox_id:
        options = page.locator(f'{_attr_selector(listbox_id)} [role="option"]')
    else:
        options = page.locator(
            '.select__menu [role="option"], [role="listbox"] [role="option"]'
        )
    try:
        count = min(options.count(), 100)
    except Exception:
        return False
    fallback = None
    for index in range(count):
        option = options.nth(index)
        try:
            if not option.is_visible(timeout=300):
                continue
            text = option.inner_text(timeout=500).strip()
        except Exception:
            continue
        normalized = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
        if normalized == desired:
            option.click(timeout=3000)
            return True
        if (
            desired
            and len(desired) > 3
            and (desired in normalized or normalized in desired)
        ):
            fallback = option
    if fallback is not None:
        fallback.click(timeout=3000)
        return True
    return False


def _open_combobox(page: Any, locator: Any, close_existing: bool = True) -> None:
    if close_existing:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
    locator.scroll_into_view_if_needed(timeout=5000)
    try:
        button = locator.locator(
            'xpath=ancestor::div[contains(@class,"select__control")][1]//button'
        ).first
        if button.count() > 0:
            button.click(timeout=3000)
            return
    except Exception:
        pass
    locator.click(timeout=5000)


def _fill_control(page: Any, locator: Any, value: str, field_label: str = "") -> str:
    role = ""
    described_by = ""
    try:
        role = str(locator.get_attribute("role") or "")
        described_by = str(locator.get_attribute("aria-describedby") or "")
    except Exception:
        pass
    is_combobox = role == "combobox" or "react-select" in described_by
    if not is_combobox:
        locator.fill(value)
        return "filled"

    selected: list[str] = []
    last_error = ""
    if "mark all that apply" in field_label.lower():
        values = [item.strip() for item in re.split(r"[;\n]+", value) if item.strip()]
    else:
        values = [value]
    for raw_value in values or [value]:
        terms = _combobox_candidate_terms(raw_value, field_label)
        selected_one = False
        for term in terms:
            try:
                _open_combobox(page, locator, close_existing=not bool(selected))
                page.wait_for_timeout(300)
                if _click_combobox_option(page, locator, term):
                    selected.append(term)
                    selected_one = True
                    page.wait_for_timeout(300)
                    break
                locator.fill(term)
                page.wait_for_timeout(500)
                if _click_combobox_option(page, locator, term):
                    selected.append(term)
                    selected_one = True
                    page.wait_for_timeout(300)
                    break
                locator.press("Enter")
                page.wait_for_timeout(300)
                selected.append(term)
                selected_one = True
                break
            except Exception as exc:
                last_error = str(exc)[:160]
        if not selected_one:
            raise RuntimeError(
                last_error or f"combobox selection failed for {raw_value}"
            )
    return f"selected_option:{', '.join(selected)}"


def _collect_validation_state(page: Any) -> list[dict[str, str]]:
    script = """
    () => {
      const clean = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
      const selectorFor = (el) => {
        if (el.id) return `#${el.id}`;
        if (el.name) return `[name="${String(el.name).replace(/"/g, '\\"')}"]`;
        return el.tagName ? el.tagName.toLowerCase() : '';
      };
      const candidates = new Set([
        ...document.querySelectorAll(':invalid'),
        ...document.querySelectorAll('[aria-invalid="true"]'),
        ...document.querySelectorAll('.field_with_errors input, .field_with_errors textarea, .field_with_errors select'),
        ...document.querySelectorAll('.error input, .error textarea, .error select'),
      ]);
      return Array.from(candidates).slice(0, 40).map((el) => {
        const label = el.labels && el.labels.length
          ? Array.from(el.labels).map((node) => clean(node.innerText || node.textContent)).join(' ')
          : '';
        const describedBy = clean(el.getAttribute('aria-describedby'));
        const describedText = describedBy
          ? describedBy.split(/\\s+/).map((id) => {
              const node = document.getElementById(id);
              return node ? clean(node.innerText || node.textContent) : '';
            }).filter(Boolean).join(' ')
          : '';
        const parentText = el.closest('.field, .field_with_errors, .error, label, div')
          ? clean(el.closest('.field, .field_with_errors, .error, label, div').innerText || '')
          : '';
        return {
          selector: selectorFor(el),
          name: clean(el.getAttribute('name')),
          label: clean(label),
          message: clean(el.validationMessage || describedText || parentText),
          value: clean(el.value).slice(0, 120),
        };
      });
    }
    """
    out: list[dict[str, str]] = []
    for frame in page.frames:
        try:
            frame_items = frame.evaluate(script)
        except Exception:
            continue
        if isinstance(frame_items, list):
            out.extend(item for item in frame_items if isinstance(item, dict))
    return out


def _run_instructions(
    page: Any,
    instructions: list[FormFillInstruction],
    results: list[dict[str, str]],
    *,
    step_name: str = "",
) -> None:
    for instruction in instructions:
        field_label = f"{step_name} / {instruction.field_label}" if step_name else instruction.field_label
        if not instruction.selector or instruction.action in {"manual", "skip"}:
            results.append(
                {"field": field_label, "action": instruction.action, "status": "skipped"}
            )
            continue
        try:
            target = _target_frame(page, instruction.frame_url)
            locator = target.locator(instruction.selector).first
            if instruction.action == "fill":
                status = _fill_control(page, locator, instruction.value, instruction.field_label)
                results.append({"field": field_label, "action": instruction.action, "status": status})
                continue
            if instruction.action == "upload":
                if not instruction.file_path or not Path(instruction.file_path).exists():
                    results.append({"field": field_label, "action": "upload", "status": "missing_file"})
                    continue
                locator.set_input_files(instruction.file_path)
            elif instruction.action == "check":
                status = _check_control(locator)
                results.append({"field": field_label, "action": instruction.action, "status": status})
                continue
            elif instruction.action == "select":
                locator.select_option(label=instruction.value)
            results.append({"field": field_label, "action": instruction.action, "status": "filled"})
        except Exception as exc:
            results.append(
                {"field": field_label, "action": instruction.action, "status": f"error: {exc}"[:180]}
            )


def _advance_explicit_portal_step(page: Any, selector: str) -> str:
    try:
        locator = page.locator(selector).first
        if locator.count() == 0:
            return "continue_not_found"
        if locator.is_disabled():
            return "continue_disabled"
        locator.click(timeout=5000)
        page.wait_for_timeout(800)
        return "continued"
    except Exception as exc:
        return f"continue_error: {exc}"[:180]


def fill_form_with_playwright(
    plan: FormFillPlan,
    headless: bool = False,
    timeout_ms: int = 20000,
    submit: bool = False,
) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    results: list[dict[str, str]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        page = browser.new_page()
        response_log: list[dict[str, str | int]] = []

        def record_response(response: Any) -> None:
            if len(response_log) >= 12:
                return
            try:
                url = str(response.url)
                method = str(response.request.method)
                lower = url.lower()
                if method != "GET" or any(
                    token in lower
                    for token in [
                        "send_application",
                        "application",
                        "/apply",
                        "lever.co",
                    ]
                ):
                    response_log.append(
                        {"method": method, "status": response.status, "url": url}
                    )
            except Exception:
                return

        page.on("response", record_response)
        page.goto(plan.apply_url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(1000)
        _open_application_form(page, plan.apply_url)
        _remove_known_interaction_blockers(page)
        try:
            initial_text = page.locator("body").inner_text(timeout=2000)[:4000]
        except Exception:
            initial_text = ""
        portal_state = classify_portal_state(
            url=page.url, page_text=initial_text, plan=plan
        )
        if portal_state["state"] != "ready":
            final_url = page.url
            validation = _collect_validation_state(page)
            browser.close()
            result = {
                "submit_allowed": plan.submit_allowed,
                "submit_requested": submit,
                "submit": "blocked_portal_gate",
                **portal_evidence(portal_state),
                "final_url": final_url,
                "page_text_excerpt": initial_text[:1000],
                "validation": validation,
                "responses": response_log,
                "results": results,
            }
            result.update(evaluate_submit_evidence(result))
            return result
        _run_instructions(page, plan.instructions, results)
        for step in plan.portal_steps:
            try:
                current_text = page.locator("body").inner_text(timeout=2000)[:4000]
            except Exception:
                current_text = ""
            portal_state = classify_portal_state(
                url=page.url, page_text=current_text, plan=plan
            )
            if portal_state["state"] != "ready":
                break
            status = _advance_explicit_portal_step(page, step.continue_selector)
            results.append({"field": step.name, "action": "continue", "status": status})
            if status != "continued":
                break
            try:
                step_text = page.locator("body").inner_text(timeout=2000)[:4000]
            except Exception:
                step_text = ""
            portal_state = classify_portal_state(url=page.url, page_text=step_text, plan=plan)
            if portal_state["state"] != "ready":
                break
            _run_instructions(page, step.instructions, results, step_name=step.name)
        try:
            submit_gate_text = page.locator("body").inner_text(timeout=2000)[:4000]
        except Exception:
            submit_gate_text = ""
        portal_state = classify_portal_state(
            url=page.url, page_text=submit_gate_text, plan=plan
        )
        submit_result = {"submit": "blocked"}
        if submit and plan.submit_allowed and portal_state["state"] == "ready":
            blockers = submission_blockers(plan)
            failed_required = [
                item["field"]
                for item in results
                if item.get("status") in {"skipped", "missing_file"}
                or str(item.get("status", "")).startswith("error:")
                or (
                    item.get("action") == "continue"
                    and item.get("status") != "continued"
                )
            ]
            if blockers or failed_required:
                reason = "; ".join([*blockers, *[f"Field not completed: {item}" for item in failed_required]])
                submit_result = {"submit": f"blocked: {reason}"}
            else:
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    page.wait_for_timeout(2000)
                _remove_known_interaction_blockers(page)
                submit_result = _click_submit_control(page)
                page.wait_for_timeout(3000)
        final_url = page.url
        try:
            page_text_excerpt = page.locator("body").inner_text(timeout=2000)[:1000]
        except Exception:
            page_text_excerpt = ""
        validation = _collect_validation_state(page)
        browser.close()
    result = {
        "submit_allowed": plan.submit_allowed,
        "submit_requested": submit,
        **submit_result,
        **portal_evidence(portal_state),
        **_join_completion_guard(plan),
        "final_url": final_url,
        "page_text_excerpt": page_text_excerpt,
        "validation": validation,
        "responses": response_log,
        "results": results,
    }
    result.update(evaluate_submit_evidence(result))
    return result
