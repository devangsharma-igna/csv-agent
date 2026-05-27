interface RadioGroupProps {
  label: string;
  options: string[];
  value: string;
  onChange: (v: string) => void;
  help?: string;
}

export function RadioGroup({ label, options, value, onChange, help }: RadioGroupProps) {
  return (
    <div className="space-y-1.5">
      <label className="block text-sm font-medium text-[#0f172a]">{label}</label>
      <div className="flex flex-wrap gap-3">
        {options.map(opt => (
          <label key={opt} className="flex items-center gap-2 cursor-pointer">
            <input
              type="radio"
              name={label}
              value={opt}
              checked={value === opt}
              onChange={() => onChange(opt)}
              className="accent-[#1a56db]"
            />
            <span className="text-sm text-[#475569]">{opt}</span>
          </label>
        ))}
      </div>
      {help && <p className="text-xs text-[#6e8ea3]">{help}</p>}
    </div>
  );
}
