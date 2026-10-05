import axios from 'axios';

const apiClient = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

apiClient.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('access_token');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error)
);

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('access_token');
      localStorage.removeItem('refresh_token');
      /**
       * 401 回登录页 —— **必须带 BASE_URL**。
       *
       * 与 LoginPage 登录成功后的跳转同一类缺陷（2026-10-05 走查 P0-3）：
       * 产物挂在 `/ui`，写死 '/login' 会把医生甩到**后端**的 /login（404），
       * 表现为"令牌一过期整个界面就打不开"，而根因只是一行硬编码路径。
       * BASE_URL 由 Vite 注入：生产 '/ui/' → '/ui/login'，dev '/' → '/login'。
       */
      const loginUrl = `${import.meta.env.BASE_URL}login`;
      if (!window.location.pathname.endsWith(loginUrl)) {
        window.location.href = loginUrl;
      }
    }
    return Promise.reject(error);
  }
);

export default apiClient;
