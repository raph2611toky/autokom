import { Moon, Sun } from 'lucide-react'

export default function ThemeToggle({ theme, onToggle, className = '' }) {
  const dark = theme === 'dark'
  return (
    <button
      type="button"
      className={`ak-theme-toggle ${className}`.trim()}
      onClick={onToggle}
      title={dark ? 'Passer au mode clair' : 'Passer au mode sombre'}
      aria-label={dark ? 'Passer au mode clair' : 'Passer au mode sombre'}
      aria-pressed={dark}
    >
      {dark ? <Sun size={19} strokeWidth={1.8} /> : <Moon size={19} strokeWidth={1.8} />}
    </button>
  )
}
