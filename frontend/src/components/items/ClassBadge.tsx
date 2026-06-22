// frontend/src/components/items/ClassBadge.tsx

interface Props {
  classLabel: string;
}

/**
 * Read-only pill showing the item's class label.
 * Never an input — usItemClass is derived from item data and not editable
 * in the XML editor.
 */
export default function ClassBadge({ classLabel }: Props) {
  return (
    <span className="badge bg-wasteland-700 text-wasteland-200 border border-wasteland-600">
      {classLabel}
    </span>
  );
}
