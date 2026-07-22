"use client";

type Props = {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
};

export function PromptBox({ value, onChange, placeholder }: Props) {
  return (
    <textarea
      className="prompt"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={
        placeholder ||
        "Describe the edits… e.g. Replace July with August, replace logo with logo.png, add watermark bottom-right"
      }
      rows={4}
    />
  );
}
