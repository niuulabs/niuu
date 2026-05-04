import { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronDown, Lock, Plus, Unlock, X } from 'lucide-react';
import styles from './SessionTerminalTabBar.module.css';

interface TerminalTab {
  id: string;
  label: string;
  cliType: string;
  restricted?: boolean;
}

const CLI_OPTIONS = [
  { id: 'bash', label: 'Bash' },
  { id: 'zsh', label: 'Zsh' },
  { id: 'fish', label: 'Fish' },
  { id: 'claude', label: 'Claude' },
  { id: 'codex', label: 'Codex' },
  { id: 'aider', label: 'Aider' },
];

interface SessionTerminalTabBarProps {
  tabs: TerminalTab[];
  activeTabId: string;
  onSelectTab: (id: string) => void;
  onCloseTab: (id: string) => void;
  onAddTab: () => void;
  onAddCliTab?: (cliType: string) => void;
}

export function SessionTerminalTabBar({
  tabs,
  activeTabId,
  onSelectTab,
  onCloseTab,
  onAddTab,
  onAddCliTab,
}: SessionTerminalTabBarProps) {
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [dropdownPos, setDropdownPos] = useState<{ top: number; left: number } | null>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const addBtnRef = useRef<HTMLButtonElement>(null);

  const handleOptionClick = useCallback(
    (cliType: string) => {
      setDropdownOpen(false);
      if (onAddCliTab) {
        onAddCliTab(cliType);
        return;
      }
      onAddTab();
    },
    [onAddTab, onAddCliTab],
  );

  useEffect(() => {
    if (!dropdownOpen) {
      return;
    }

    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setDropdownOpen(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [dropdownOpen]);

  return (
    <div className={styles.tabBarWrapper}>
      <div className={styles.tabBar} role="tablist">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            role="tab"
            aria-selected={tab.id === activeTabId}
            data-active={tab.id === activeTabId}
            className={styles.tab}
            onClick={() => onSelectTab(tab.id)}
          >
            {tab.restricted ? (
              <Lock className={styles.modeIcon} />
            ) : (
              <Unlock className={styles.modeIcon} />
            )}
            <span className={styles.tabLabel}>{tab.label}</span>
            {tabs.length > 1 && (
              <span
                role="button"
                aria-label={`Close ${tab.label}`}
                className={styles.closeButton}
                onClick={(event) => {
                  event.stopPropagation();
                  onCloseTab(tab.id);
                }}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.stopPropagation();
                    onCloseTab(tab.id);
                  }
                }}
                tabIndex={0}
              >
                <X className={styles.closeIcon} />
              </span>
            )}
          </button>
        ))}
        <div className={styles.addContainer} ref={dropdownRef}>
          <button
            ref={addBtnRef}
            className={styles.addButton}
            onClick={() => {
              setDropdownOpen((prev) => {
                if (!prev && addBtnRef.current) {
                  const rect = addBtnRef.current.getBoundingClientRect();
                  setDropdownPos({ top: rect.bottom + 4, left: rect.left });
                }
                return !prev;
              });
            }}
            aria-label="New terminal"
            aria-expanded={dropdownOpen}
            aria-haspopup="menu"
          >
            <Plus className={styles.addIcon} />
            <ChevronDown className={styles.chevronIcon} />
          </button>
          {dropdownOpen && dropdownPos && (
            <div
              className={styles.dropdown}
              role="menu"
              style={{ position: 'fixed', top: dropdownPos.top, left: dropdownPos.left }}
            >
              {CLI_OPTIONS.map((option) => (
                <button
                  key={option.id}
                  role="menuitem"
                  className={styles.dropdownItem}
                  onClick={() => handleOptionClick(option.id)}
                >
                  {option.label}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
