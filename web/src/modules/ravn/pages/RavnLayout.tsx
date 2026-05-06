import { NavLink, Outlet } from 'react-router-dom';
import { cn } from '@/modules/shared/utils/classnames';
import styles from './RavnLayout.module.css';

const navItems = [
  { to: 'chat', label: 'Chat' },
  { to: 'sessions', label: 'Sessions' },
  { to: 'personas', label: 'Personas' },
  { to: 'config', label: 'Config' },
];

export function RavnLayout() {
  return (
    <div className={styles.layout}>
      <header className={styles.header}>
        <div className={styles.brand}>
          <span className={styles.rune}>&#x16B3;</span>
          <span className={styles.brandName}>Ravn</span>
        </div>
        <nav className={styles.nav}>
          {navItems.map(item => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) => cn(styles.navLink, isActive && styles.navLinkActive)}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </header>
      <main className={styles.content}>
        <Outlet />
      </main>
    </div>
  );
}
