import { LucideIcon } from "lucide-react";

export default function EmptyState({
  Icon,
  title,
  subtitle,
}: {
  Icon: LucideIcon;
  title: string;
  subtitle?: string;
}) {
  return (
    <div className="flex flex-col items-center text-center py-16 px-6">
      <div className="w-16 h-16 rounded-full bg-primary-50 flex items-center justify-center mb-4">
        <Icon size={30} className="text-primary" strokeWidth={1.8} />
      </div>
      <p className="text-lg font-semibold text-deep">{title}</p>
      {subtitle && (
        <p className="text-sm text-stone-500 mt-1 max-w-xs">{subtitle}</p>
      )}
    </div>
  );
}
