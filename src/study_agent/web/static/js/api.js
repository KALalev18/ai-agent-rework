// Thin fetch wrapper. POSTs carry the X-Study-Agent header the server requires (CSRF guard).

const HEADERS = { "Content-Type": "application/json", "X-Study-Agent": "1" };

async function handle(response) {
  if (response.ok) return response.json();
  const body = await response.json().catch(() => ({}));
  throw new Error(body.detail || `${response.status} ${response.statusText}`);
}

export const api = {
  get: (path) => fetch(path).then(handle),
  post: (path, body = {}) => fetch(path, { method: "POST", headers: HEADERS, body: JSON.stringify(body) }).then(handle),
  preview: (name, args) => api.post(`/api/actions/${name}/preview`, args),
  action: (name, args) => api.post(`/api/actions/${name}`, args),
};
