import { createRoute } from '@tanstack/react-router';
import { Boxes } from 'lucide-react';
import { definePlugin } from '@niuulabs/plugin-sdk';
import { RealmsHomePage } from './ui/RealmsHomePage';
import { NewRealmPage } from './ui/NewRealmPage';
import { RealmPage } from './ui/RealmPage';
import { RealmSettingsPage } from './ui/RealmSettingsPage';

export const REALMS_TABS = [
  { id: 'all', label: 'All', path: '/realms' },
  { id: 'needs-you', label: 'Needs you', path: '/realms/needs-you' },
  { id: 'templates', label: 'Templates', path: '/realms/templates' },
] as const;

export const realmsPlugin = definePlugin({
  id: 'realms',
  rune: 'ᚱ',
  title: 'Realms',
  subtitle: 'environments kept by residents',
  tabs: REALMS_TABS.map((tab) => ({ ...tab })),
  simple: {
    tabs: REALMS_TABS.map((tab) => tab.id),
    // A set of environments, each its own box.
    icon: <Boxes size={17} aria-hidden="true" />,
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
    createRoute({ getParentRoute: () => rootRoute, path: '/realms/$slug', component: RealmPage }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/realms/$slug/settings',
      component: RealmSettingsPage,
    }),
  ],
});

export { RealmsHomePage } from './ui/RealmsHomePage';
export { NewRealmPage } from './ui/NewRealmPage';
export { RealmPage } from './ui/RealmPage';
export { RealmSettingsPage } from './ui/RealmSettingsPage';
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
