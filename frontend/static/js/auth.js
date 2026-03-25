export async function apiFetch(url, options = {}) {
    const token = fbAuth.currentUser
        ? await fbAuth.currentUser.getIdToken()
        : null;
    const headers = { "Content-Type": "application/json", ...options.headers };
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const res = await fetch(url, { ...options, headers });
    if (!res.ok) {
        const err = await res.json().catch(() => ({ error: res.statusText }));
        throw new Error(err.error || err.message || "Request failed");
    }
    return res.json();
}
