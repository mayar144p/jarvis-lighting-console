// Signing in to GDTF Share where the desk needs it.  The makers' own 3D
// bodies and gobo pictures come from their GDTF files on GDTF Share (a free
// account at gdtf-share.com): the 3D asks for a sign-in right there, not
// "go to Add lights first".  The password stays in this app's memory only.
import { h, modal, toast } from "./ui.js";
import { post } from "./api.js";

/** The sign-in box; resolves true once signed in (false: cancelled). */
export function gdtfSignIn(why = "") {
  return new Promise((resolve) => {
    const user = h("input", { type: "text", placeholder: "gdtf-share.com user", autocomplete: "username", "aria-label": "GDTF Share user" });
    const pass = h("input", { type: "password", placeholder: "password", autocomplete: "current-password", "aria-label": "GDTF Share password" });
    const msg = h("div.muted.small", why || "Sign in to GDTF Share. The password stays in this app's memory only.");
    let done = false;
    const finish = (ok) => { if (!done) { done = true; resolve(ok); } };
    const go = async () => {
      if (!user.value.trim() || !pass.value) { msg.textContent = "Your gdtf-share.com user and password"; return; }
      msg.textContent = "Signing in…";
      const d = await post("/api/gdtf/login", { user: user.value.trim(), password: pass.value }).catch((e) => ({ error: e.message }));
      if (d.error) { msg.textContent = d.error; return; }
      toast("Signed in to GDTF Share", "ok");
      finish(true);
      close();
    };
    pass.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
    user.addEventListener("keydown", (e) => { if (e.key === "Enter") pass.focus(); });
    const close = modal({
      title: "Sign in to GDTF Share",
      body: h("div.form-grid",
        h("label.field", h("span", "User"), user),
        h("label.field", h("span", "Password"), pass),
        msg,
        h("p.muted.small", "No account? It's free: ",
          h("a", { href: "https://gdtf-share.com", target: "_blank", rel: "noopener noreferrer" }, "gdtf-share.com"),
          ". The password stays in this app's memory only.")),
      foot: [h("button.btn", { onclick: () => close() }, "Cancel"), h("button.btn.primary", { onclick: go }, "Sign in")],
      onClose: () => finish(false),
    });
    setTimeout(() => user.focus(), 50);
  });
}
