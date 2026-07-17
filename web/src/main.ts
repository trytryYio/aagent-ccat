import { createApp } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import App from './App.vue'
import ChatView from './views/ChatView.vue'
import SettingsView from './views/SettingsView.vue'
import KnowledgeView from './views/KnowledgeView.vue'
import './assets/main.css'

const routes = [
  { path: '/', name: 'chat', component: ChatView },
  { path: '/settings', name: 'settings', component: SettingsView },
  { path: '/knowledge', name: 'knowledge', component: KnowledgeView },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

createApp(App).use(router).mount('#app')
