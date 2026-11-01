import { createRoute } from '@tanstack/react-router';
import { RealmsIcon } from '@niuulabs/ui';
import { definePlugin } from '@niuulabs/plugin-sdk';
import { RealmsHomePage } from './ui/RealmsHomePage';
import { NewRealmPage } from './ui/NewRealmPage';
import { RealmPage } from './ui/RealmPage';
import { RealmSettingsPage } from './ui/RealmSettingsPage';
import { ProjectPage } from './ui/ProjectPage';

export const REALMS_TABS = [
  { id: 'all', label: 'All', path: '/realms' },
  { id: 'needs-you', label: 'Needs you', path: '/realms/needs-you' },
  { id: 'templates', label: 'Templates', path: '/realms/templates' },
] as const;

export const realmsPlugin = definePlugin({
  id: 'realms',
  rune: 'R',
  // A set of environments, each its own box.
  icon: <RealmsIcon />,
  title: 'Workspaces',
  subtitle: 'where work happens',
  tabs: REALMS_TABS.map((tab) => ({ ...tab })),
  simple: {
    tabs: REALMS_TABS.map((tab) => tab.id),
  },
  routes: (rootRoute) => [
    createRoute({ getParentRoute: () => rootRoute, path: '/realms', component: RealmsHomePage }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/realms/needs-you',
      component: () => <RealmsHomePage view="needs-you" />,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/realms/templates',
      component: () => <RealmsHomePage view="templates" />,
    }),
    createRoute({ getParentRoute: () => rootRoute, path: '/realms/new', component: NewRealmPage }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/realms/projects/$projectId',
      component: ProjectPage,
    }),
    createRoute({ getParentRoute: () => rootRoute, path: '/realms/$slug', component: RealmPage }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/realms/$slug/settings',
      component: RealmSettingsPage,
    }),
  ],
});

export { homePlugin } from './home/plugin';
export { HomePage } from './home/HomePage';
export { RealmsHomePage } from './ui/RealmsHomePage';
export { NewRealmPage } from './ui/NewRealmPage';
export { RealmPage } from './ui/RealmPage';
export { RealmSettingsPage } from './ui/RealmSettingsPage';
export { ProjectPage } from './ui/ProjectPage';
export { REALM_TEMPLATES, templateById, type RealmTemplate } from './domain/templates';
export { parseSentence, type SentenceDraft } from './domain/sentence';
export {
  personaNameFor,
  residentNameFor,
  mountNameFor,
  routingRuleIdFor,
  charterPagePathFor,
  type RealmView,
} from './domain/realm';
