import { registerModuleDefinition } from '@/modules/shared/registry';
import { RaidhoRune } from '@/modules/shared/registry/rune-icons';

registerModuleDefinition({
  key: 'ravn',
  label: 'Ravn',
  icon: RaidhoRune,
  basePath: '/ravn',
  layout: () => import('./pages/RavnLayout').then(m => ({ default: m.RavnLayout })),
  routes: [
    { path: '', index: true, redirectTo: 'chat' },
    {
      path: 'chat',
      load: () => import('./pages/ChatView').then(m => ({ default: m.ChatView })),
    },
    {
      path: 'sessions',
      load: () => import('./pages/SessionsView').then(m => ({ default: m.SessionsView })),
    },
    {
      path: 'config',
      load: () => import('./pages/AgentsView').then(m => ({ default: m.AgentsView })),
    },
    {
      path: 'personas',
      load: () => import('./pages/PersonasView').then(m => ({ default: m.PersonasView })),
    },
    {
      path: 'personas/:name',
      load: () => import('./pages/PersonaDetailView').then(m => ({ default: m.PersonaDetailView })),
    },
  ],
});
