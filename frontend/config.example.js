window.MTIP_CONFIG = Object.freeze({
  cognitoDomain: "https://YOUR_COGNITO_DOMAIN",
  clientId: "YOUR_COGNITO_APP_CLIENT_ID",
  redirectUri: "http://localhost:8000/",
  logoutUri: "http://localhost:8000/",
  apiBaseUrl: "https://YOUR_API_ID.execute-api.ap-south-1.amazonaws.com",
  scopes: ["openid", "email"]
});
