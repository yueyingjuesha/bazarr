// Sonarr
type SonarrSeriesType = "Standard" | "Daily" | "Anime";

type PythonBoolean = "True" | "False";

type FileTree = {
  children: boolean;
  path: string;
  name: string;
};

type StorageType = string | null;

type SelectorOption<PAYLOAD> = {
  label: string;
  value: PAYLOAD;
};

type SelectorValueType<T, M extends boolean> = M extends true
  ? ReadonlyArray<T>
  : Nullable<T>;

type SimpleStateType<T> = [
  T,
  ((item: T) => void) | ((fn: (item: T) => T) => void)
];

type Factory<T> = () => T;
